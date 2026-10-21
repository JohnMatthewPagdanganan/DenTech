const uploadForm = document.getElementById('imaging-upload');
uploadForm?.addEventListener('submit', () => {
  const button = document.getElementById('upload-button');
  button.disabled = true;
  button.textContent = 'Uploading & preparing preview…';
  document.getElementById('upload-progress').textContent = 'Please keep this page open. DICOM scans are validated and prepared before the viewer opens.';
});
