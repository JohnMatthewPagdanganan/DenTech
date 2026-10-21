from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from .models import User,Patient,Visit,Document,ToothRecord

class DentalChartTests(TestCase):
    def setUp(self):
        self.dentist=User.objects.create_user('dentist',email='dentist@example.test',role='dentist')
        self.user=User.objects.create_user('patient',email='patient@example.test',role='patient')
        self.other=User.objects.create_user('other',email='other@example.test',role='patient')
        self.reception=User.objects.create_user('reception',email='reception@example.test',role='reception')
        self.patient=Patient.objects.create(user=self.user,first_name='Test',last_name='Patient',birth_date='2000-01-01')
        self.visit=Visit.objects.create(patient=self.patient,dentist=self.dentist,date=timezone.localdate(),title='Test',released=True)
        self.doc=Document.objects.create(visit=self.visit,title='X-ray',file='private/test.png',kind='image',state='ready',released=True)
        self.tooth=ToothRecord.objects.create(updated_by=self.dentist,patient=self.patient,number=16,condition='decay',notes='PRIVATE CLINICAL NOTE',patient_notes='Please discuss treatment.',released=True)
        ToothRecord.objects.create(updated_by=self.dentist,patient=self.patient,number=17,condition='filled',patient_notes='UNSHARED NOTE',released=False)
        self.url=f'/documents/{self.doc.pk}/data/dental-chart/'
    def test_patient_sees_only_shared_findings_and_no_internal_notes(self):
        self.client.force_login(self.user)
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        self.assertEqual([t['number'] for t in response.json()['teeth']],[16])
        self.assertNotContains(response,'PRIVATE CLINICAL NOTE')
        self.assertNotContains(response,'UNSHARED NOTE')
        self.assertEqual(response.json()['scope'],'current')
    def test_staff_preview_includes_unshared_but_not_internal_notes(self):
        self.client.force_login(self.dentist)
        response=self.client.get(self.url)
        self.assertEqual(len(response.json()['teeth']),2)
        self.assertNotContains(response,'PRIVATE CLINICAL NOTE')
    def test_other_patient_and_reception_denied(self):
        for user in [self.other,self.reception]:
            self.client.force_login(user)
            self.assertIn(self.client.get(self.url).status_code,[403,404])
    def test_parent_and_document_release_required(self):
        self.client.force_login(self.user)
        self.doc.released=False;self.doc.save()
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.doc.released=True;self.doc.save()
        self.visit.released=False;self.visit.save()
        self.assertEqual(self.client.get(self.url).status_code,404)
    def test_withdrawing_tooth_hides_current_finding(self):
        self.client.force_login(self.user)
        self.tooth.released=False;self.tooth.save()
        self.assertEqual(self.client.get(self.url).json()['teeth'],[])
    def test_image_and_pdf_offer_model_without_cbct(self):
        self.client.force_login(self.user)
        for kind in ['image','pdf']:
            self.doc.kind=kind;self.doc.file='private/test.'+('png' if kind=='image' else 'pdf');self.doc.save()
            response=self.client.get(f'/documents/{self.doc.pk}/view/')
            self.assertContains(response,'3D dental chart')
            self.assertContains(response,'dental-model.js')



    def test_dentist_can_save_shared_notes_and_withdraw(self):
        from django.urls import reverse
        self.client.force_login(self.dentist)
        url=reverse('clinical_create',args=[self.patient.pk,'tooth'])
        data={'number':16,'condition':'filled','notes':'INTERNAL','patient_notes':'Filling completed.','released':'on'}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.tooth.refresh_from_db()
        self.assertTrue(self.tooth.released)
        self.assertEqual(self.tooth.patient_notes,'Filling completed.')
        data.pop('released')
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.tooth.refresh_from_db()
        self.assertFalse(self.tooth.released)

class AnatomicalAssetTests(SimpleTestCase):
    def test_reference_geometry_integrity_and_tooth_coverage(self):
        import hashlib,json
        import numpy as np
        from pathlib import Path
        from django.conf import settings
        directory=Path(settings.BASE_DIR)/'static'/'dental-anatomy'
        manifest=json.loads((directory/'manifest.json').read_text())
        binary=(directory/manifest['binary']).read_bytes()
        self.assertEqual(hashlib.sha256(binary).hexdigest(),manifest['sha256'])
        teeth=[part for part in manifest['parts'] if part.get('number')]
        self.assertEqual(len(teeth),28)
        self.assertEqual({part['number'] for part in teeth},{q*10+n for q in [1,2,3,4] for n in range(1,8)})
        for part in manifest['parts']:
            self.assertLessEqual(part['offset']+part['bytes'],len(binary))
            positions=np.frombuffer(binary,dtype='<f4',count=part['vertexCount']*3,offset=part['offset']+part['positions'])
            indices=np.frombuffer(binary,dtype='<u4',count=part['indexCount'],offset=part['offset']+part['indices'])
            self.assertTrue(np.isfinite(positions).all())
            self.assertEqual(len(indices)%3,0)
            self.assertLess(int(indices.max()),part['vertexCount'])
            if part.get('number'):
                self.assertEqual(part['jaw'],'upper' if part['number']//10 in [1,2] else 'lower')
        self.assertIn('CC Attribution 4.0',(directory/'ATTRIBUTION.txt').read_text(encoding='utf-8'))

class DemoImagingSetupTests(TestCase):
    def test_setup_is_repeatable_and_preserves_accounts_and_edited_findings(self):
        import io
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from django.core.management import call_command
        from django.test import override_settings
        from .test_imaging import scan_zip
        dentist=User.objects.create_user('demo.dentist',email='demo.dentist@example.test',role='dentist')
        user=User.objects.create_user('demo.patient',email='demo.patient@example.test',role='patient')
        patient=Patient.objects.create(user=user,first_name='Sam',last_name='Cruz',birth_date='1998-04-12')
        Visit.objects.create(patient=patient,dentist=dentist,date=timezone.localdate(),title='Demo visit',findings='Fictional demo: test fixture',released=True)
        tooth=ToothRecord.objects.create(patient=patient,number=16,condition='filled',notes='Keep edited note',updated_by=dentist)
        original_password=user.password
        with TemporaryDirectory() as directory,override_settings(DEBUG=True,MEDIA_ROOT=directory):
            archive=Path(directory)/'sample.zip';archive.write_bytes(scan_zip().read())
            for _ in range(2):call_command('seed_demo_imaging',sample_zip=archive,stdout=io.StringIO())
            self.assertEqual(Document.objects.filter(visit__patient=patient).count(),2)
            self.client.force_login(user)
            self.assertContains(self.client.get('/documents/'),'Sample 2D X-ray slice')
            for document in Document.objects.all():
                self.assertEqual(document.state,'ready')
                self.assertTrue(document.released)
                self.assertTrue(document.visit.released)
                self.assertContains(self.client.get(f'/documents/{document.pk}/view/'),'dental-anatomy/manifest.json')
        user.refresh_from_db();tooth.refresh_from_db()
        self.assertEqual(user.password,original_password)
        self.assertEqual(tooth.notes,'Keep edited note')
        self.assertEqual(tooth.condition,'filled')
        self.assertFalse(tooth.released)

    def test_setup_does_not_add_documents_to_unverified_patient(self):
        import io
        from django.core.management import call_command
        from django.test import override_settings
        with override_settings(DEBUG=True):
            call_command('seed_demo_imaging',stdout=io.StringIO())
        self.assertEqual(Document.objects.count(),0)
