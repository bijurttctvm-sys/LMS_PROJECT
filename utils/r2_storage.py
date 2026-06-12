import io

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings


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


def upload_file(file_obj, key):
    client = get_r2_client()
    client.upload_fileobj(file_obj, settings.CLOUDFLARE_R2_BUCKET_NAME, key)
    return key


def download_file(key):
    client = get_r2_client()
    buf = io.BytesIO()
    client.download_fileobj(settings.CLOUDFLARE_R2_BUCKET_NAME, key, buf)
    buf.seek(0)
    return buf.read()


def get_signed_url(key, expiry=3600):
    client = get_r2_client()
    return client.generate_presigned_url(
        'get_object',
        Params={'Bucket': settings.CLOUDFLARE_R2_BUCKET_NAME, 'Key': key},
        ExpiresIn=expiry,
    )


def delete_file(key):
    client = get_r2_client()
    client.delete_object(Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME, Key=key)
