from pathlib import Path

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError


_WINDOWS_EXECUTABLE_SIGNATURE = b'MZ'
_ELF_SIGNATURE = b'\x7fELF'
_SHEBANG_SIGNATURE = b'#!'
_ZIP_SIGNATURE = b'PK'
_OLE_SIGNATURE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
_EBML_SIGNATURE = b'\x1a\x45\xdf\xa3'


def _peek_file_bytes(uploaded_file, byte_count=512):
    if not uploaded_file or not hasattr(uploaded_file, 'read'):
        return b''

    current_position = None
    if hasattr(uploaded_file, 'tell'):
        try:
            current_position = uploaded_file.tell()
        except Exception:
            current_position = None

    try:
        if hasattr(uploaded_file, 'seek'):
            uploaded_file.seek(0)
        return uploaded_file.read(byte_count)
    finally:
        if current_position is not None and hasattr(uploaded_file, 'seek'):
            uploaded_file.seek(current_position)


def _verify_image_content(uploaded_file, label):
    current_position = None
    if hasattr(uploaded_file, 'tell'):
        try:
            current_position = uploaded_file.tell()
        except Exception:
            current_position = None

    try:
        if hasattr(uploaded_file, 'seek'):
            uploaded_file.seek(0)
        image = Image.open(uploaded_file)
        image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError(f'{label} is not a valid image.') from exc
    finally:
        if current_position is not None and hasattr(uploaded_file, 'seek'):
            uploaded_file.seek(current_position)


def _reject_known_executable_signatures(header, label):
    if header.startswith(_WINDOWS_EXECUTABLE_SIGNATURE):
        raise ValidationError(f'{label} appears to be a Windows executable, which is not allowed.')
    if header.startswith(_ELF_SIGNATURE):
        raise ValidationError(f'{label} appears to be a Linux executable, which is not allowed.')
    if header.startswith(_SHEBANG_SIGNATURE):
        raise ValidationError(f'{label} appears to be a script file, which is not allowed.')


def validate_study_material_signature(header, extension, label):
    _reject_known_executable_signatures(header, label)

    if extension == '.pdf' and not header.startswith(b'%PDF'):
        raise ValidationError(f'{label} must be a valid PDF document.')
    if extension in {'.docx', '.pptx'} and not header.startswith(_ZIP_SIGNATURE):
        raise ValidationError(f'{label} must be a valid Office Open XML document.')
    if extension == '.ppt' and not header.startswith(_OLE_SIGNATURE):
        raise ValidationError(f'{label} must be a valid PowerPoint document.')
    if extension == '.txt' and b'\x00' in header:
        raise ValidationError(f'{label} must be a plain text file.')


def validate_video_signature(header, extension, label):
    _reject_known_executable_signatures(header, label)

    if extension in {'.mp4', '.mov'} and b'ftyp' not in header[:32]:
        raise ValidationError(f'{label} must be a valid MP4 or MOV video file.')
    if extension == '.avi' and not (header.startswith(b'RIFF') and header[8:12] == b'AVI '):
        raise ValidationError(f'{label} must be a valid AVI video file.')
    if extension in {'.mkv', '.webm'} and not header.startswith(_EBML_SIGNATURE):
        raise ValidationError(f'{label} must be a valid MKV or WebM video file.')


def validate_uploaded_file(
    uploaded_file,
    *,
    allowed_extensions=None,
    allowed_content_types=None,
    max_bytes=None,
    label='File',
    signature_validator=None,
    verify_image=False,
):
    if not uploaded_file:
        return uploaded_file

    extension = Path(uploaded_file.name or '').suffix.lower()
    if allowed_extensions:
        normalised_extensions = {
            ext if ext.startswith('.') else f'.{ext}'
            for ext in allowed_extensions
        }
        if extension not in normalised_extensions:
            raise ValidationError(
                f'{label} type is not allowed. Accepted types: '
                f'{", ".join(sorted(normalised_extensions))}.'
            )

    if max_bytes and uploaded_file.size > max_bytes:
        max_mb = max_bytes / (1024 * 1024)
        raise ValidationError(
            f'{label} must be smaller than {max_mb:.0f} MB.'
        )

    content_type = (getattr(uploaded_file, 'content_type', '') or '').lower()
    if allowed_content_types and content_type:
        matches_allowed_type = False
        for allowed_type in allowed_content_types:
            allowed_type = allowed_type.lower()
            if allowed_type.endswith('/*'):
                if content_type.startswith(allowed_type[:-1]):
                    matches_allowed_type = True
                    break
            elif content_type == allowed_type:
                matches_allowed_type = True
                break
        if not matches_allowed_type:
            raise ValidationError(f'{label} content type is not allowed.')

    header = _peek_file_bytes(uploaded_file)
    if signature_validator and header:
        signature_validator(header, extension, label)

    if verify_image:
        _verify_image_content(uploaded_file, label)

    return uploaded_file
