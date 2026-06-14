import io
import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import boto3
from django.conf import settings


def uses_object_storage():
    return all((
        getattr(settings, 'CLOUDFLARE_R2_ACCESS_KEY_ID', None),
        getattr(settings, 'CLOUDFLARE_R2_SECRET_ACCESS_KEY', None),
        getattr(settings, 'CLOUDFLARE_R2_BUCKET_NAME', None),
        getattr(settings, 'CLOUDFLARE_R2_ENDPOINT_URL', None)
        or getattr(settings, 'CLOUDFLARE_R2_ENDPOINT', None),
    ))


def get_r2_client():
    endpoint = (
        getattr(settings, 'CLOUDFLARE_R2_ENDPOINT_URL', None)
        or getattr(settings, 'CLOUDFLARE_R2_ENDPOINT', None)
    )
    return boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=settings.CLOUDFLARE_R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.CLOUDFLARE_R2_SECRET_ACCESS_KEY,
        region_name='auto',
    )


def _normalise_key(key):
    return str(PurePosixPath(str(key).lstrip('/')))


def _local_path_for_key(key):
    safe_key = _normalise_key(key)
    return Path(settings.MEDIA_ROOT) / Path(*PurePosixPath(safe_key).parts)


def _local_url_for_key(key):
    safe_key = _normalise_key(key)
    base_url = getattr(settings, 'MEDIA_URL', '/media/').rstrip('/')
    return f'{base_url}/{quote(safe_key, safe="/")}'


def upload_file(file_obj, key):
    if not uses_object_storage():
        target = _local_path_for_key(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
        with target.open('wb') as destination:
            shutil.copyfileobj(file_obj, destination)
        return _normalise_key(key)

    client = get_r2_client()
    client.upload_fileobj(file_obj, settings.CLOUDFLARE_R2_BUCKET_NAME, key)
    return key


def download_file(key):
    if not uses_object_storage():
        return _local_path_for_key(key).read_bytes()

    client = get_r2_client()
    buf = io.BytesIO()
    client.download_fileobj(settings.CLOUDFLARE_R2_BUCKET_NAME, key, buf)
    buf.seek(0)
    return buf.read()


def get_signed_url(key, expiry=3600):
    if not uses_object_storage():
        local_path = _local_path_for_key(key)
        if not local_path.exists():
            raise FileNotFoundError(local_path)
        return _local_url_for_key(key)

    client = get_r2_client()
    return client.generate_presigned_url(
        'get_object',
        Params={'Bucket': settings.CLOUDFLARE_R2_BUCKET_NAME, 'Key': key},
        ExpiresIn=expiry,
    )


def delete_file(key):
    if not uses_object_storage():
        local_path = _local_path_for_key(key)
        if local_path.exists():
            local_path.unlink()
        return

    client = get_r2_client()
    client.delete_object(Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME, Key=key)
