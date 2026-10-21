"""Seed only the known fictional demo patient with public teaching imaging."""
import io
import shutil
import urllib.request
from pathlib import Path
import numpy as np
from PIL import Image
from django.conf import settings
from django.core.files import File
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from clinic.models import Patient, User, Visit, Document, ToothRecord
from clinic.imaging import prepare_document

SOURCE_URL = 'https://medimodel.com/sample-dicom-files/class-3-malocclusion/'
DOWNLOAD_URL = 'https://medimodel.com/wp-content/uploads/2018/08/Class-3-malocclusion.zip'
VISIT_TITLE = 'Imaging viewer demonstration'

class Command(BaseCommand):
    help = 'Add anonymized teaching documents to the known fictional demo patient; preserve credentials and real records.'

    def add_arguments(self, parser):
        parser.add_argument('--sample-zip', type=Path)
        parser.add_argument('--download-sample', action='store_true')

    def handle(self, *args, **options):
        if not settings.DEBUG and not hasattr(settings, 'DEMO_ROOT'):
            raise CommandError('Teaching fixtures are restricted to local development and isolated public demo settings.')
        patient=Patient.objects.filter(user__username='demo.patient',user__role='patient',first_name='Sam',last_name='Cruz').first()
        dentist=User.objects.filter(username='demo.dentist',role='dentist').first()
        if not patient or not dentist or not patient.visits.filter(findings__startswith='Fictional demo:').exists():
            self.stdout.write('Known fictional demo records were not found. No accounts or patient records were changed.')
            return
        visit, _=Visit.objects.get_or_create(patient=patient,title=VISIT_TITLE,defaults={
            'dentist':dentist,'date':timezone.localdate(),'released':True,
            'findings':'Anonymized Medimodel teaching scan. This is not this fictional patient\'s anatomy. For viewer demonstration only.'})
        document=visit.documents.filter(kind='cbct',metadata__source_url=SOURCE_URL,state='ready').first()
        if not document:
            archive=options['sample_zip'] or Path(settings.BASE_DIR)/'.demo-assets'/'dental-sample.zip'
            if not archive.exists() and options['download_sample']:
                archive.parent.mkdir(parents=True,exist_ok=True)
                temporary=archive.with_suffix('.download')
                try:
                    with urllib.request.urlopen(DOWNLOAD_URL,timeout=60) as response,temporary.open('wb') as output:
                        length=0
                        while chunk:=response.read(1024*1024):
                            length+=len(chunk)
                            if length>96*1024*1024:raise CommandError('Teaching scan exceeded the upload size limit.')
                            output.write(chunk)
                    temporary.replace(archive)
                finally:
                    temporary.unlink(missing_ok=True)
            if not archive.exists():
                self.stdout.write('Teaching scan not installed. Run seed_demo_imaging --download-sample once to add it.')
                return
            with archive.open('rb') as file:
                document=Document.objects.create(visit=visit,title='Sample dental CT - Medimodel teaching scan',released=True,
                    file=File(file,name='teaching-scan.zip'),state='processing',metadata={'source_url':SOURCE_URL})
            prepare_document(document)
            if document.state!='ready':raise CommandError(document.error_message)
            document.metadata['source_url']=SOURCE_URL;document.save(update_fields=['metadata'])
        if not visit.documents.filter(kind='image',metadata__source_url=SOURCE_URL).exists():
            with document.volume.open('rb') as file:
                values=np.frombuffer(file.read(),dtype='<f4').reshape(list(reversed(document.metadata['dimensions'])))
            low,high=document.metadata['window'];image=values[len(values)//2]
            pixels=np.clip((image-low)/(high-low)*255,0,255).astype(np.uint8)
            output=io.BytesIO();Image.fromarray(pixels).save(output,format='PNG')
            Document.objects.create(visit=visit,title='Sample 2D X-ray slice - teaching scan',released=True,kind='image',state='ready',
                file=ContentFile(output.getvalue(),name='teaching-slice.png'),metadata={'source_url':SOURCE_URL})
        for number,condition in [(16,'decay'),(22,'filled'),(36,'missing'),(41,'healthy'),(11,'crown'),(46,'treatment')]:
            tooth,created=ToothRecord.objects.get_or_create(patient=patient,number=number,defaults={
                'condition':condition,'patient_notes':'Fictional demonstration finding. Discuss with the dentist.',
                'released':True,'updated_by':dentist})
            if not created and tooth.condition=='healthy' and not tooth.notes and not tooth.patient_notes and not tooth.released:
                tooth.condition=condition;tooth.patient_notes='Fictional demonstration finding. Discuss with the dentist.'
                tooth.released=True;tooth.updated_by=dentist;tooth.save()
        self.stdout.write(self.style.SUCCESS('Teaching documents ready for demo.patient. Existing passwords and other patient records were preserved.'))
        self.stdout.write('/documents/')
