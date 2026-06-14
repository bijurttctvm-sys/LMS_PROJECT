from pathlib import Path

from django.core.exceptions import ValidationError


def validate_uploaded_file(
    uploaded_file,
    *,
    allowed_extensions=None,
    allowed_content_types=None,
    max_bytes=None,
    label='File',
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

    return uploaded_file
