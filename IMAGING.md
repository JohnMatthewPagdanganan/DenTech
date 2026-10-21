# Documents and imaging

## Using the viewer

Dentists and administrators open **Documents > Upload documents**, select the patient's visit, then choose one or more files. **Attach document** on a visit opens the same form for that visit.

- **PNG/JPEG:** interactive 2D image with zoom, pan, rotation and reset.
- **PDF:** embedded browser PDF viewer with original download fallback.
- **DICOM series ZIP or multi-frame DCM:** 2D scan cross-sections and a **2D view / Interactive 3D** switch. 3D supports drag rotation, zoom, pan, visibility threshold, opacity, contrast and reset.

Existing image/PDF attachments also open in the viewer. Ordinary 2D files show a disabled 3D option with an explanation; they are never converted into invented 3D anatomy. CT volume rendering displays the captured scan voxels, not an automatically segmented tooth model.

The page adapts to screen width. 3D requires browser WebGL 2 graphics support. If unavailable, the viewer keeps the 2D slices available. Download originals for use with an appropriate clinical viewer.

## Visibility and privacy

Use **Details & visibility** to rename the document or change **Visible to patient** after reviewing it. Both the document and its visit must be released before the owning patient can list, view or download it. Receptionists have no imaging access. All original and preview endpoints repeat the same ownership/role checks, and private files are not served from a public media URL. Preview JSON includes only geometry and display metadata, not DICOM patient names/IDs.

The application does not verify a scan's patient identity against the selected clinic record. The uploading clinician must confirm this. Original DICOM files can contain identifying information; download access remains restricted accordingly.

## Compatible DICOM exports

Upload one regular, complete CT/CBCT volume per ZIP, with grayscale slices, a shared series identifier, image positions/orientation and pixel spacing. Files in a ZIP can be extensionless standard DICOM Part 10 files. DICOMDIR and non-image extras are ignored; unsafe/encrypted archive entries are rejected. Multiple series, mixed patient identifiers, duplicate/missing/uneven slices, varying dimensions/orientation and sheared/gantry-tilted stacks are rejected rather than displayed with misleading geometry.

A single multi-frame CT DICOM is supported when it contains per-frame positions and suitable spatial metadata. Vendor-private/missing geometry and arbitrary 2D DICOM exports are not supported. Some scanner exports require conversion by the scanner's software to a regular DICOM series. NumPy/pydicom perform decoding; installed pylibjpeg plugins support common JPEG/JPEG 2000 encodings, but not every transfer syntax is guaranteed.

## Limits

- Up to 12 attachments per upload and 96 MiB total per request.
- PDF/PNG/JPEG: 10 MiB per file; 2D images limited to 25 megapixels.
- DICOM ZIP/DCM: 96 MiB per file.
- ZIP: up to 2,048 files and 512 MiB expanded, with per-entry/compression-ratio bounds.
- Volume: up to 256 million source voxels. Source dimensions and geometry are validated before decoding.
- Browser preview: sampled to at most 192 voxels along each axis, with corresponding voxel spacing. The original file remains unchanged.

Scan preparation currently runs during upload, with a visible busy message; it is not a background task queue. Ready/Preparing/Unable to prepare states are stored. Failed scans remain private and show a specific recovery message with upload/retry options for clinical staff.

This is a review/education preview, not a validated diagnostic, measurement or treatment-planning tool. It does not identify pathology, segment individual teeth, or reconstruct anatomy from regular radiographs. CBCT intensity values are not labeled as calibrated Hounsfield units. Cross-section labels follow scan axes instead of claiming universal anatomical orientation.

## Dependencies and build

Install updated `requirements.txt`, then run `python manage.py migrate`. The bundled viewer is already in `static/`; Node.js is needed only if editing the viewer source:

```powershell
cd frontend
npm ci
npm run build
```

The frontend uses VTK.js 37.4.6 with a locked dependency tree and a locally bundled viewer; medical scan data is not sent to a third-party viewer/CDN. Third-party license notices accompany the bundle. Run `python manage.py test clinic` for the application/imaging regression suite.

For public-server changes, run `collectstatic` using the public settings and restart the public application, or stop/start the public demo (which creates a new tunnel URL). The Waitress body limit is 100 MiB to accommodate the bounded upload plus multipart overhead. Configure equivalent limits on any other production proxy.

## Teaching sample used during validation

The viewer was exercised with Medimodel's anonymized **Class 3 malocclusion** dental CT scan: https://medimodel.com/sample-dicom-files/class-3-malocclusion/. Medimodel offers its samples for education and research: https://medimodel.com/sample-dicom-files/.

The example has 640 × 640 × 517 source voxels and was rendered using a 160 × 160 × 173 browser preview. Validation fixtures and source scan files are outside the repository and do not modify real clinic records. The sample is not bundled with the repo or automatically added to the public demo. Review the source terms before redistributing scans publicly.


## Illustrative dental chart
Every ready image, PDF and CBCT document offers a 3D dental chart. This is a generic colored model linked to the patient's CURRENT FDI chart, not reconstructed anatomy or a historical snapshot. The original remains in 2D; CBCT also retains the actual 3D scan tab.
Dentists use Patient > Dental chart > Update tooth, enter the condition and separate patient-facing notes, then select Share this tooth condition with the patient. Existing internal notes never enter the viewer API. Unshared and unrecorded teeth remain neutral for patients; gray does not mean healthy. Staff can preview unshared findings. Both document and parent visit must be released for patient access. Rotation, zoom, tooth hover/tap, jaw and primary/permanent selectors, optional animation, and keyboard tooth buttons are supported. No CBCT is required for this illustrative model.


## Anatomical reference replacement
The 3D dental chart now uses locally hosted BodyParts3D 4.0 anatomy instead of procedural tooth and jaw shapes: a matching mandible, two gingiva meshes, and 28 separate permanent teeth, approximately 1.04 MB of geometry. Each tooth is mapped to its FDI number before condition tinting and selection. The original source mesh geometry is retained; the viewer recenters/scales the assembly, opens the lower jaw slightly and clips upper root tips above the gingiva for the dental view.
This reference contains no primary teeth or third molars. Their findings remain accessible through the numbered chart; the UI explicitly states that their 3D geometry is unavailable. Selecting Primary teeth shows the numbered findings without substituting adult anatomy.
Attribution and source provenance are in static/dental-anatomy/ATTRIBUTION.txt and linked in the viewer. Geometry license: CC BY 4.0. No patient data or external model service is used to load the anatomy. Existing document permissions and per-tooth sharing rules still apply to the findings.


## Consistent local and public demonstrations
Code and static anatomy assets are shared by all launch modes. Databases and private media remain separate so local patient records are never published by the demo tunnel. The `seed_demo_imaging` management command installs the same released teaching CT, 2D slice and illustrative sample findings for the verified fictional `demo.patient` (Sam Cruz) in the selected database. It preserves existing passwords and edited findings, and does nothing if the known fictional fixture markers are absent.
Run `.\.venv\Scripts\python.exe manage.py seed_demo_imaging --download-sample` once for local demo data. Both `start.ps1` and public demo startup also run this idempotent setup. The 21 MB public teaching archive is downloaded once to ignored `.demo-assets/`, not served as a public static file or committed. Each mode stores its own authenticated document copies. Source: Medimodel Class 3 malocclusion teaching scan; the sample is explicitly labeled and is not the fictional patient's anatomy. To see it, sign in as demo.patient, or as clinical staff, then open Documents. Actual patient uploads are intentionally not synchronized to the public demo.
After changing templates while a server is running with `--noreload`, restart that server; after static changes refresh the browser. Public deployment also requires collectstatic and restarting its app process.
