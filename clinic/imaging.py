"""Bounded DICOM-to-preview conversion. Originals remain private and unchanged.

Supports regular single-series CT slices and spatially described multi-frame CT.
Generated previews are downsampled for review, not diagnostic measurements.
"""
import io
import logging
import math
from pathlib import Path, PurePosixPath
import stat
import zipfile
import numpy as np
import pydicom
from PIL import Image
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)
MAX_ARCHIVE = 96 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_VOXELS = 256_000_000
PREVIEW_EDGE = 192


def validate_upload(file):
    suffix = Path(file.name).suffix.lower()
    if suffix not in ['.pdf', '.png', '.jpg', '.jpeg', '.zip', '.dcm']:
        raise ValidationError('Choose a PDF, PNG, JPEG, DICOM ZIP, or multi-frame DCM file.')
    limit = MAX_ARCHIVE if suffix in ['.zip', '.dcm'] else 10 * 1024 * 1024
    if file.size > limit:
        raise ValidationError('DICOM uploads must be 96 MB or smaller.' if limit == MAX_ARCHIVE else 'Files must be 10 MB or smaller.')
    head = file.read(132)
    file.seek(0)
    valid = {'.pdf': head.startswith(b'%PDF-'), '.png': head.startswith(b'\x89PNG\r\n\x1a\n'),
             '.jpg': head.startswith(b'\xff\xd8\xff'), '.jpeg': head.startswith(b'\xff\xd8\xff'),
             '.zip': head.startswith(b'PK\x03\x04'), '.dcm': head[128:132] == b'DICM'}
    if not valid[suffix]:
        raise ValidationError('File content does not match its extension. Use a valid image, PDF, or DICOM export.')
    if suffix in ['.png', '.jpg', '.jpeg']:
        try:
            with Image.open(io.BytesIO(file.read())) as image:
                if image.width * image.height > 25_000_000:
                    raise ValidationError('This 2D image is too large for the browser viewer. Use an image below 25 megapixels.')
                image.verify()
        except ValidationError:
            raise
        except Exception as error:
            raise ValidationError('This image is incomplete or cannot be read. Upload a valid PNG or JPEG.') from error
        finally:
            file.seek(0)
    return file


def _sequence_value(ds, sequence, keyword, default=None):
    values = getattr(ds, sequence, [])
    return getattr(values[0], keyword, default) if values else default


def _geometry(ds, frame=None):
    shared = getattr(ds, 'SharedFunctionalGroupsSequence', [])
    shared = shared[0] if shared else ds
    frame = frame if frame is not None else ds
    orientation = _sequence_value(frame, 'PlaneOrientationSequence', 'ImageOrientationPatient',
        _sequence_value(shared, 'PlaneOrientationSequence', 'ImageOrientationPatient', getattr(ds, 'ImageOrientationPatient', None)))
    position = _sequence_value(frame, 'PlanePositionSequence', 'ImagePositionPatient', getattr(ds, 'ImagePositionPatient', None))
    spacing = _sequence_value(frame, 'PixelMeasuresSequence', 'PixelSpacing',
        _sequence_value(shared, 'PixelMeasuresSequence', 'PixelSpacing', getattr(ds, 'PixelSpacing', None)))
    if orientation is None or position is None or spacing is None:
        raise ValidationError('This scan lacks spatial positions, orientation, or pixel spacing. Export the complete DICOM volume from the scanner.')
    orientation, position, spacing = (np.asarray(value, dtype=float) for value in [orientation, position, spacing])
    if orientation.shape != (6,) or position.shape != (3,) or spacing.shape != (2,):
        raise ValidationError('Unsupported scan geometry. Ask for a standard CT/CBCT DICOM export.')
    if not all(np.isfinite(v).all() for v in [orientation, position, spacing]) or np.any(spacing <= 0):
        raise ValidationError('The scan contains invalid spatial values.')
    x, y = orientation[:3], orientation[3:]
    if not np.isclose(np.linalg.norm(x), 1, atol=.001) or not np.isclose(np.linalg.norm(y), 1, atol=.001) or abs(np.dot(x, y)) > .001:
        raise ValidationError('The scan orientation is not supported.')
    return orientation, position, spacing


def _validate_header(ds):
    if str(getattr(ds, 'Modality', '')) != 'CT':
        raise ValidationError('Choose a CT/CBCT DICOM volume. A panoramic or other 2D DICOM is not a 3D scan.')
    rows, cols = int(getattr(ds, 'Rows', 0)), int(getattr(ds, 'Columns', 0))
    if not 2 <= rows <= 4096 or not 2 <= cols <= 4096 or int(getattr(ds, 'BitsAllocated', 0)) not in [8, 16, 32]:
        raise ValidationError('Unsupported or invalid DICOM pixel dimensions.')
    if int(getattr(ds, 'SamplesPerPixel', 1)) != 1 or str(getattr(ds, 'PhotometricInterpretation', '')) not in ['MONOCHROME1', 'MONOCHROME2']:
        raise ValidationError('Only grayscale CT/CBCT volumes are supported.')
    if not getattr(ds, 'SeriesInstanceUID', None):
        raise ValidationError('The DICOM series identifier is missing.')
    return rows, cols


def read_volume(file):
    """Return safe geometry metadata and little-endian float32 volume bytes (X fastest)."""
    file.seek(0)
    archive = None
    try:
        if Path(file.name).suffix.lower() == '.zip':
            archive = zipfile.ZipFile(file)
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            if len(entries) > 2048 or sum(e.file_size for e in entries) > MAX_EXPANDED:
                raise ValidationError('The ZIP expands beyond the supported scan size (512 MB or 2,048 files).')
            for entry in entries:
                path = PurePosixPath(entry.filename.replace('\\', '/'))
                mode = entry.external_attr >> 16
                if path.is_absolute() or '..' in path.parts or ':' in entry.filename or stat.S_ISLNK(mode) or entry.flag_bits & 1:
                    raise ValidationError('Unsafe or encrypted ZIP entries are not supported. Export a plain DICOM series ZIP.')
                if entry.file_size > 128 * 1024 * 1024 or entry.file_size / max(1, entry.compress_size) > 1500:
                    raise ValidationError('An archive entry exceeds the supported file size or compression ratio.')
            def read(name, header=False):
                with archive.open(name) as stream:
                    return pydicom.dcmread(stream, stop_before_pixels=header)
            sources = [e.filename for e in entries if not e.filename.startswith('__MACOSX/') and PurePosixPath(e.filename).name.upper() != 'DICOMDIR']
        else:
            def read(name, header=False):
                file.seek(0)
                return pydicom.dcmread(file, stop_before_pixels=header)
            sources = [file.name]
        headers = []
        for name in sources:
            try:
                header = read(name, header=True)
            except pydicom.errors.InvalidDicomError:
                if Path(name).suffix.lower() == '.dcm':
                    raise ValidationError('A DICOM file in this scan is unreadable or incomplete.')
                continue
            if hasattr(header, 'Rows') and hasattr(header, 'Columns'):
                headers.append((name, header))
        if not headers:
            raise ValidationError('No readable DICOM images were found in this upload.')
        if len({str(h.SeriesInstanceUID) for _, h in headers if hasattr(h, 'SeriesInstanceUID')}) != 1:
            raise ValidationError('The ZIP contains multiple scan series. Upload exactly one complete CT/CBCT series per ZIP.')
        # Reject mixed patients, even if a malformed export reuses a series UID.
        if len({str(getattr(h, 'PatientID', '')) for _, h in headers}) > 1:
            raise ValidationError('The upload contains images from different patients.')
        frames = []
        for name, header in headers:
            shape = _validate_header(header)
            count = int(getattr(header, 'NumberOfFrames', 1))
            if not 1 <= count <= 2048:
                raise ValidationError('Unsupported number of scan frames.')
            if count > 1:
                per_frame = getattr(header, 'PerFrameFunctionalGroupsSequence', [])
                if len(headers) != 1 or len(per_frame) != count:
                    raise ValidationError('Multi-frame scans need one file with complete per-frame spatial metadata.')
                for index, frame in enumerate(per_frame):
                    frames.append((name, header, index, _geometry(header, frame), shape))
            else:
                frames.append((name, header, None, _geometry(header), shape))
        if len(frames) < 3:
            raise ValidationError('A 3D scan needs at least three spatially located slices. Ordinary X-rays cannot be combined into a volume.')
        rows, cols = frames[0][4]
        if rows * cols * len(frames) > MAX_VOXELS:
            raise ValidationError('This volume is too large for this viewer. Ask for a smaller scanner export.')
        orient, _, pixel_spacing = frames[0][3]
        normal = np.cross(orient[:3], orient[3:])
        for _, header, _, geometry, shape in frames:
            if shape != (rows, cols) or not np.allclose(geometry[0], orient, atol=.001) or not np.allclose(geometry[2], pixel_spacing, atol=.001):
                raise ValidationError('Slice dimensions or orientation vary. Upload a single regular CT/CBCT series.')
        frames.sort(key=lambda entry: float(np.dot(entry[3][1], normal)))
        positions = np.array([entry[3][1] for entry in frames])
        distances = np.diff(positions @ normal)
        dz = float(np.median(distances))
        if dz <= 0 or not np.allclose(distances, dz, atol=max(.02, dz * .05), rtol=0):
            raise ValidationError('Slices are duplicated, missing, or unevenly spaced. Upload the complete regular series.')
        if not np.allclose(np.diff(positions, axis=0), normal * dz, atol=.05, rtol=0):
            raise ValidationError('Sheared/tilted slice stacks are not supported by this preview. Use a regular reconstructed export.')
        sz, sy, sx = [max(1, math.ceil(size / PREVIEW_EDGE)) for size in [len(frames), rows, cols]]
        preview = []
        multi_dataset = None
        for name, header, index, geometry, shape in frames[::sz]:
            ds = multi_dataset if multi_dataset is not None else read(name)
            if index is not None:
                multi_dataset = ds
                pixels = pydicom.pixels.pixel_array(ds, index=index)
                per = ds.PerFrameFunctionalGroupsSequence[index]
            else:
                pixels = ds.pixel_array
                per = ds
            shared = getattr(ds, 'SharedFunctionalGroupsSequence', [])
            shared = shared[0] if shared else ds
            slope = _sequence_value(per, 'PixelValueTransformationSequence', 'RescaleSlope',
                _sequence_value(shared, 'PixelValueTransformationSequence', 'RescaleSlope', getattr(ds, 'RescaleSlope', 1)))
            intercept = _sequence_value(per, 'PixelValueTransformationSequence', 'RescaleIntercept',
                _sequence_value(shared, 'PixelValueTransformationSequence', 'RescaleIntercept', getattr(ds, 'RescaleIntercept', 0)))
            pixels = pixels[::sy, ::sx].astype(np.float32) * float(slope) + float(intercept)
            if not np.isfinite(pixels).all():
                raise ValidationError('Scan intensities contain invalid values.')
            preview.append(pixels)
        volume = np.stack(preview).astype('<f4')
        low, high = [float(value) for value in np.percentile(volume, [1, 99.5])]
        if high <= low:
            low, high = float(volume.min()), float(volume.max())
        if high <= low:
            raise ValidationError('This scan has no usable intensity variation.')
        metadata = {'dimensions': list(reversed(volume.shape)),
            'spacing': [float(pixel_spacing[1] * sx), float(pixel_spacing[0] * sy), dz * sz],
            'origin': positions[0].tolist(), 'direction': [*orient[:3].tolist(), *orient[3:].tolist(), *normal.tolist()],
            'range': [float(volume.min()), float(volume.max())], 'window': [low, high],
            'source_dimensions': [cols, rows, len(frames)], 'downsampled': any(step > 1 for step in [sx, sy, sz]),
            'photometric': str(headers[0][1].PhotometricInterpretation), 'format': 'float32-le', 'version': 1}
        return metadata, volume.tobytes()
    except ValidationError:
        raise
    except (RuntimeError, NotImplementedError) as error:
        raise ValidationError('This DICOM encoding could not be decoded. Export uncompressed CT/CBCT DICOM or install a compatible decoder.') from error
    except Exception as error:
        raise ValidationError('The scan could not be read. Check that the upload is a complete, valid CT/CBCT DICOM series.') from error
    finally:
        if archive:
            archive.close()


def prepare_document(document):
    extension = Path(document.file.name).suffix.lower()
    document.kind = 'cbct' if extension in ['.zip', '.dcm'] else 'pdf' if extension == '.pdf' else 'image'
    if document.kind != 'cbct':
        document.state = 'ready'
        document.save(update_fields=['kind', 'state'])
        return
    document.state = 'processing'
    document.error_message = ''
    document.save(update_fields=['kind', 'state', 'error_message'])
    try:
        with document.file.open('rb') as file:
            metadata, raw = read_volume(file)
        document.volume.save('preview.f32', ContentFile(raw), save=False)
        document.metadata = metadata
        document.state = 'ready'
    except ValidationError as error:
        document.state = 'failed'
        document.released = False
        document.error_message = ' '.join(error.messages)[:300]
    except Exception:
        logger.exception('Imaging preparation failed for document %s', document.pk)
        document.state = 'failed'
        document.released = False
        document.error_message = 'Unable to prepare this scan. Ask clinic staff to review the upload.'
    document.save()
