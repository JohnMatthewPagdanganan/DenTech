import io
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from PIL import Image
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from pydicom.dataset import FileDataset, FileMetaDataset, Dataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, CTImageStorage, EnhancedCTImageStorage, generate_uid
from .imaging import read_volume, prepare_document, validate_upload
from .models import Document, User, Patient, Visit


def slice_bytes(index, series, patient='TEST', shape=(8, 8), position=None, orientation=None):
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    ds = FileDataset(None, {}, file_meta=meta, preamble=b'\0' * 128)
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.SeriesInstanceUID = series
    ds.StudyInstanceUID = generate_uid()
    ds.PatientID = patient
    ds.PatientName = 'PRIVATE^TESTNAME'
    ds.Modality = 'CT'
    ds.Rows, ds.Columns = shape
    ds.ImagePositionPatient = position or [0, 0, index * .5]
    ds.ImageOrientationPatient = orientation or [1, 0, 0, 0, 1, 0]
    ds.PixelSpacing = [.4, .3]
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = 'MONOCHROME2'
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 1
    ds.RescaleSlope = 2
    ds.RescaleIntercept = -100
    ds.PixelData = (np.arange(shape[0] * shape[1]).reshape(shape) + index * 100).astype('<i2').tobytes()
    stream = io.BytesIO()
    ds.save_as(stream, enforce_file_format=True)
    return stream.getvalue()


def scan_zip(items=None):
    series = generate_uid()
    if items is None:
        items = [(f'{index}.dcm', slice_bytes(index, series)) for index in reversed(range(4))]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in items:
            archive.writestr(name, content)
    return SimpleUploadedFile('scan.zip', stream.getvalue(), 'application/zip')


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ImagingTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(dir=settings.BASE_DIR)
        self.override = override_settings(MEDIA_ROOT=self.directory.name)
        self.override.enable()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.override.disable)
        self.dentist = User.objects.create_user('imaging.dentist', 'id@example.test', 'test-password', role='dentist')
        self.user = User.objects.create_user('imaging.patient', 'ip@example.test', 'test-password')
        self.other_user = User.objects.create_user('imaging.other', 'io@example.test', 'test-password')
        self.reception = User.objects.create_user('imaging.reception', 'ir@example.test', 'test-password', role='reception')
        attrs = dict(first_name='Fictional', last_name='Patient', birth_date='2000-01-01', phone='09000000000')
        self.patient = Patient.objects.create(user=self.user, **attrs)
        self.other = Patient.objects.create(user=self.other_user, **attrs)
        self.visit = Visit.objects.create(patient=self.patient, dentist=self.dentist, date=timezone.localdate(), title='Test imaging', findings='Test', released=True)

    def document(self, released=True):
        document = Document.objects.create(visit=self.visit, title='Test scan', file=scan_zip(), released=released)
        prepare_document(document)
        self.assertEqual(document.state, 'ready', document.error_message)
        return document

    def test_real_voxels_sort_spacing_and_rescale(self):
        metadata, raw = read_volume(scan_zip())
        voxels = np.frombuffer(raw, dtype='<f4').reshape(4, 8, 8)
        self.assertEqual(metadata['dimensions'], [8, 8, 4])
        self.assertEqual(metadata['spacing'], [.3, .4, .5])
        self.assertEqual(voxels[0, 0, 0], -100)
        self.assertEqual(voxels[3, 0, 0], 500)
        self.assertNotIn('PRIVATE', str(metadata))
        self.assertNotIn('PatientID', metadata)

    def test_zip_path_traversal_rejected(self):
        with self.assertRaisesMessage(ValidationError, 'Unsafe'):
            read_volume(scan_zip([('../outside.dcm', b'bad')]))
        self.assertFalse((Path(self.directory.name) / 'outside.dcm').exists())

    def test_mixed_series_rejected(self):
        items = [(f'{i}.dcm', slice_bytes(i, generate_uid())) for i in range(4)]
        with self.assertRaisesMessage(ValidationError, 'multiple scan series'):
            read_volume(scan_zip(items))

    def test_different_patients_rejected(self):
        series = generate_uid()
        items = [(f'{i}.dcm', slice_bytes(i, series, patient=str(i))) for i in range(4)]
        with self.assertRaisesMessage(ValidationError, 'different patients'):
            read_volume(scan_zip(items))

    def test_single_2d_xray_is_not_a_volume(self):
        with self.assertRaisesMessage(ValidationError, 'at least three'):
            read_volume(scan_zip([('single.dcm', slice_bytes(0, generate_uid()))]))

    def test_duplicate_slices_rejected(self):
        series = generate_uid()
        items = [(f'{i}.dcm', slice_bytes(0, series)) for i in range(4)]
        with self.assertRaisesMessage(ValidationError, 'duplicated'):
            read_volume(scan_zip(items))

    def test_irregular_spacing_rejected(self):
        series = generate_uid()
        items = [(f'{i}.dcm', slice_bytes(i, series, position=[0, 0, z])) for i, z in enumerate([0, .5, 1, 4])]
        with self.assertRaisesMessage(ValidationError, 'unevenly spaced'):
            read_volume(scan_zip(items))

    def test_dimension_mismatch_rejected(self):
        series = generate_uid()
        items = [(f'{i}.dcm', slice_bytes(i, series, shape=(8, 9) if i == 3 else (8, 8))) for i in range(4)]
        with self.assertRaisesMessage(ValidationError, 'dimensions or orientation vary'):
            read_volume(scan_zip(items))

    def test_multi_frame_dicom_with_spatial_metadata(self):
        import pydicom
        ds = pydicom.dcmread(io.BytesIO(slice_bytes(0, generate_uid())))
        ds.SOPClassUID = EnhancedCTImageStorage
        ds.file_meta.MediaStorageSOPClassUID = EnhancedCTImageStorage
        ds.NumberOfFrames = 4
        groups = []
        for i in range(4):
            group = Dataset()
            position = Dataset()
            position.ImagePositionPatient = [0, 0, i * .5]
            group.PlanePositionSequence = Sequence([position])
            groups.append(group)
        ds.PerFrameFunctionalGroupsSequence = Sequence(groups)
        ds.PixelData = np.arange(4 * 8 * 8, dtype='<i2').tobytes()
        stream = io.BytesIO()
        ds.save_as(stream, enforce_file_format=True)
        metadata, raw = read_volume(SimpleUploadedFile('volume.dcm', stream.getvalue()))
        self.assertEqual(metadata['dimensions'], [8, 8, 4])
        self.assertEqual(len(raw), 4 * 8 * 8 * 4)

    def test_oblique_direction_is_preserved(self):
        series = generate_uid()
        orientation = [0, 1, 0, -1, 0, 0]
        metadata, raw = read_volume(scan_zip([(f'{i}.dcm', slice_bytes(i, series, orientation=orientation)) for i in range(4)]))
        self.assertEqual(metadata['direction'][:6], orientation)

    def test_invalid_disguised_file_rejected(self):
        with self.assertRaises(ValidationError):
            validate_upload(SimpleUploadedFile('fake.png', b'%PDF-1.4 fake'))

    def test_valid_image_is_verified_and_rewound(self):
        content = io.BytesIO()
        Image.new('L', (8, 8), 100).save(content, format='PNG')
        file = SimpleUploadedFile('xray.png', content.getvalue())
        self.assertIs(validate_upload(file), file)
        self.assertEqual(file.tell(), 0)

    def test_broken_image_with_valid_magic_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, 'incomplete'):
            validate_upload(SimpleUploadedFile('broken.png', b'\x89PNG\r\n\x1a\n' + b'bad' * 10))

    def test_failed_processing_retains_original(self):
        document = Document.objects.create(visit=self.visit, title='Bad scan', file=scan_zip([('empty.txt', b'hello')]))
        prepare_document(document)
        self.assertEqual(document.state, 'failed')
        self.assertTrue(document.file.storage.exists(document.file.name))
        self.assertFalse(document.volume)

    def test_patient_can_use_released_viewer_and_preview_endpoints(self):
        document = self.document()
        self.client.force_login(self.user)
        page = self.client.get(f'/documents/{document.pk}/view/')
        self.assertContains(page, '2D view')
        self.assertContains(page, '3D dental chart')
        meta = self.client.get(f'/documents/{document.pk}/data/metadata/')
        self.assertEqual(meta.json()['dimensions'], [8, 8, 4])
        raw = self.client.get(f'/documents/{document.pk}/data/volume/')
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(raw['Cache-Control'], 'no-store, private')
        raw.close()

    def test_other_patient_and_reception_cannot_load_any_preview(self):
        document = self.document()
        for user, expected in [(self.other_user, 404), (self.reception, 403)]:
            self.client.force_login(user)
            for endpoint in ['view/', 'data/metadata/', 'data/volume/', 'data/content/']:
                with self.subTest(role=user.role, endpoint=endpoint):
                    self.assertEqual(self.client.get(f'/documents/{document.pk}/{endpoint}').status_code, expected)

    def test_unreleased_visit_or_document_blocks_preview(self):
        document = self.document(released=False)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(f'/documents/{document.pk}/view/').status_code, 404)
        document.released = True
        document.save()
        self.visit.released = False
        self.visit.save()
        self.assertEqual(self.client.get(f'/documents/{document.pk}/data/volume/').status_code, 404)

    def test_multiple_uploads_from_document_section(self):
        self.client.force_login(self.dentist)
        files = [SimpleUploadedFile('one.pdf', b'%PDF-1.4 demo'), SimpleUploadedFile('two.pdf', b'%PDF-1.4 other')]
        response = self.client.post('/documents/upload/', {'visit': self.visit.pk, 'title': 'Reports', 'files': files})
        self.assertRedirects(response, '/documents/')
        self.assertEqual(Document.objects.count(), 2)
        self.assertTrue(all(not doc.released for doc in Document.objects.all()))

    def test_valid_scan_upload(self):
        self.client.force_login(self.dentist)
        response = self.client.post(f'/visits/{self.visit.pk}/upload/', {'title': 'CBCT', 'files': scan_zip()})
        document = Document.objects.get()
        self.assertRedirects(response, f'/documents/{document.pk}/view/')
        self.assertEqual(document.kind, 'cbct')
        self.assertEqual(document.state, 'ready')

    def test_patient_cannot_upload_or_change_visibility(self):
        document = self.document()
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/documents/upload/').status_code, 403)
        self.assertEqual(self.client.post(f'/documents/{document.pk}/edit/', {'title': 'Changed', 'released': True}).status_code, 403)

    def test_clinician_can_release_and_withdraw_document(self):
        document = self.document(released=False)
        self.client.force_login(self.dentist)
        self.client.post(f'/documents/{document.pk}/edit/', {'title': document.title, 'released': True})
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(f'/documents/{document.pk}/view/').status_code, 200)
        self.client.force_login(self.dentist)
        self.client.post(f'/documents/{document.pk}/edit/', {'title': document.title})
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(f'/documents/{document.pk}/view/').status_code, 404)

    def test_pdf_preview_has_correct_headers(self):
        document = Document.objects.create(visit=self.visit, title='Report', file=SimpleUploadedFile('report.pdf', b'%PDF-1.4 demo'), released=True)
        self.client.force_login(self.user)
        response = self.client.get(f'/documents/{document.pk}/data/content/')
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        self.assertIn('inline', response['Content-Disposition'])
        response.close()

    def test_multi_file_input_validates_every_file(self):
        self.client.force_login(self.dentist)
        files = [SimpleUploadedFile('ok.pdf', b'%PDF-1.4 demo'), SimpleUploadedFile('bad.png', b'bad')]
        response = self.client.post('/documents/upload/', {'visit': self.visit.pk, 'title': 'Reports', 'files': files})
        self.assertContains(response, 'File content does not match')
        self.assertFalse(Document.objects.exists())

