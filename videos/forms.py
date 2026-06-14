from django import forms
from django.conf import settings

from courses.models import Course
from utils.upload_validators import (
    validate_study_material_signature,
    validate_uploaded_file,
    validate_video_signature,
)
from users.models import User

from .models import Video


class VideoUploadForm(forms.ModelForm):
    VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.mov', '.avi', '.webm'}

    video_file = forms.FileField(
        required=False,
        help_text='MP4, MKV, MOV, AVI, WEBM accepted. Optional — you can add study material without a video.',
        widget=forms.ClearableFileInput(attrs={'accept': 'video/*'}),
    )

    class Meta:
        model = Video
        fields = ('course', 'title', 'description', 'language_code')

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and user.role == User.Role.INSTRUCTOR:
            self.fields['course'].queryset = Course.objects.filter(
                instructor=user,
                is_active=True,
            )
        else:
            self.fields['course'].queryset = Course.objects.filter(is_active=True)

    def clean_video_file(self):
        video_file = self.cleaned_data.get('video_file')
        return validate_uploaded_file(
            video_file,
            allowed_extensions=self.VIDEO_EXTENSIONS,
            allowed_content_types={'video/*', 'application/octet-stream'},
            max_bytes=settings.MAX_VIDEO_UPLOAD_BYTES,
            label='Video file',
            signature_validator=validate_video_signature,
        )


class StudyMaterialUploadForm(forms.Form):
    ACCEPTED_EXTENSIONS = {'.pdf', '.docx', '.pptx', '.ppt', '.txt'}

    english_content = forms.CharField(
        widget=forms.Textarea(attrs={
            'rows': 14,
            'placeholder': 'Paste the course content or study notes in English here...',
            'class': 'form-control font-monospace',
            'style': 'font-size:.85rem;',
        }),
        required=False,
        label='English Study Material',
        help_text='Paste the full text, or upload a file below.',
    )
    malayalam_content = forms.CharField(
        widget=forms.Textarea(attrs={
            'rows': 8,
            'placeholder': 'Optional: paste the Malayalam version here...',
            'class': 'form-control',
            'style': 'font-size:.9rem;line-height:1.8;',
        }),
        required=False,
        label='Malayalam Content (optional)',
        help_text='Leave blank to skip Malayalam content.',
    )
    material_file = forms.FileField(
        required=False,
        label='Upload File',
        help_text='PDF, Word (.docx), PowerPoint (.pptx/.ppt), or plain text (.txt). Overwrites pasted text above.',
        widget=forms.ClearableFileInput(attrs={'accept': '.pdf,.docx,.pptx,.ppt,.txt'}),
    )

    def clean_material_file(self):
        material_file = self.cleaned_data.get('material_file')
        return validate_uploaded_file(
            material_file,
            allowed_extensions=self.ACCEPTED_EXTENSIONS,
            allowed_content_types={
                'application/pdf',
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                'application/vnd.ms-powerpoint',
                'text/plain',
                'application/octet-stream',
            },
            max_bytes=settings.MAX_STUDY_MATERIAL_BYTES,
            label='Study material file',
            signature_validator=validate_study_material_signature,
        )

    def clean_english_content(self):
        return (self.cleaned_data.get('english_content') or '').strip()

    def clean_malayalam_content(self):
        return (self.cleaned_data.get('malayalam_content') or '').strip()

    def clean(self):
        cleaned = super().clean()
        if self.errors.get('material_file'):
            return cleaned
        has_text = bool(cleaned.get('english_content', '').strip())
        has_file = bool(cleaned.get('material_file'))
        if not has_text and not has_file:
            raise forms.ValidationError(
                'Provide content either by pasting text or uploading a file.'
            )
        return cleaned
