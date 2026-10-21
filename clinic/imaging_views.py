from pathlib import Path
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .forms import ImagingUploadForm, DocumentReleaseForm
from .models import Document, Patient, Visit
from .services import audit
from .views import staff_required
from .imaging import prepare_document


def permitted_documents(user):
    rows = Document.objects.select_related('visit', 'visit__patient')
    if user.clinical_access:
        return rows
    if user.clinic_staff:
        raise PermissionDenied
    patient = get_object_or_404(Patient, user=user)
    return rows.filter(visit__patient=patient, visit__released=True, released=True)


@login_required
def documents(request):
    rows = permitted_documents(request.user)
    return render(request, 'documents.html', {'documents': rows.order_by('-pk')[:100]})


@staff_required(clinical=True)
def upload(request, pk=None):
    visit = get_object_or_404(Visit.objects.select_related('patient'), pk=pk) if pk is not None else None
    form = ImagingUploadForm(request.POST or None, request.FILES or None, visit=visit)
    if request.method == 'POST' and form.is_valid():
        selected = visit or form.cleaned_data['visit']
        files = form.cleaned_data['files']
        if sum(file.size for file in files) > 96 * 1024 * 1024:
            form.add_error('files', 'Upload at most 96 MB in a single request.')
        else:
            created = []
            for index, file in enumerate(files):
                title = form.cleaned_data['title'] + (f' ({index + 1})' if len(files) > 1 else '')
                item = Document.objects.create(visit=selected, title=title, file=file,
                    released=form.cleaned_data['released'], state='processing')
                prepare_document(item)
                created.append(item)
                audit(request.user, 'Uploaded imaging document', item.pk)
            failed = sum(item.state == 'failed' for item in created)
            if failed:
                messages.warning(request, f'{failed} scan upload(s) need attention. Open the document for details. Your original file was retained privately.')
            else:
                messages.success(request, 'Imaging uploaded. Review the preview before sharing it with the patient.')
            return redirect('document_view', pk=created[0].pk) if len(created) == 1 else redirect('documents')
    return render(request, 'imaging_upload.html', {'form': form, 'visit': visit})


@login_required
def viewer(request, pk):
    document = get_object_or_404(permitted_documents(request.user), pk=pk)
    audit(request.user, 'Viewed imaging document', document.pk)
    return render(request, 'document_viewer.html', {'document': document})


@login_required
def data(request, pk, asset):
    item = get_object_or_404(permitted_documents(request.user), pk=pk)
    if asset == 'dental-chart':
        records = item.visit.patient.teeth.all()
        if not request.user.clinical_access:
            records = records.filter(released=True)
        audit(request.user, 'Viewed interactive dental chart', item.visit.patient.pid)
        return JsonResponse({'scope': 'current', 'illustrative': True,
            'teeth': [{'number': record.number, 'condition': record.condition,
                'condition_label': record.get_condition_display(), 'notes': record.patient_notes,
                'shared': record.released, 'updated_at': record.updated_at.isoformat()}
                for record in records]})
    if asset == 'metadata':
        if item.kind != 'cbct' or item.state != 'ready' or not item.volume:
            raise Http404
        return JsonResponse(item.metadata)
    if asset == 'volume':
        if item.kind != 'cbct' or item.state != 'ready' or not item.volume:
            raise Http404
        file, mime, filename = item.volume, 'application/octet-stream', 'preview.f32'
    elif asset == 'content':
        if item.viewer_kind not in ['image', 'pdf']:
            raise Http404
        file = item.file
        suffix = Path(file.name).suffix.lower()
        mime = {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}.get(suffix)
        if not mime:
            raise Http404
        filename = 'document' + suffix
    else:
        raise Http404
    try:
        response = FileResponse(file.open('rb'), content_type=mime, as_attachment=False, filename=filename)
    except FileNotFoundError:
        raise Http404
    response['X-Frame-Options'] = 'SAMEORIGIN'
    response['X-Content-Type-Options'] = 'nosniff'
    audit(request.user, 'Loaded imaging preview', item.pk)
    return response


@staff_required(clinical=True)
def edit(request, pk):
    item = get_object_or_404(Document, pk=pk)
    form = DocumentReleaseForm(request.POST or None, instance=item)
    if request.method == 'POST' and form.is_valid():
        if form.cleaned_data['released'] and item.state != 'ready':
            form.add_error('released', 'Resolve the scan preparation error before sharing this document.')
        else:
            form.save()
            audit(request.user, 'Updated document visibility', item.pk)
            messages.success(request, 'Document details and patient visibility updated.')
            return redirect('document_view', pk=pk)
    return render(request, 'form.html', {'form': form, 'title': 'Document details & visibility',
        'subtitle': 'Patients can view this document only when both it and the parent visit are released.'})


@staff_required(clinical=True)
@require_POST
def retry(request, pk):
    item = get_object_or_404(Document, pk=pk, state='failed', kind='cbct')
    prepare_document(item)
    audit(request.user, 'Retried scan preview', item.pk)
    return redirect('document_view', pk=pk)
