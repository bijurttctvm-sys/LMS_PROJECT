from django import forms

from courses.models import Course
from users.models import User
from .models import Video


class VideoUploadForm(forms.ModelForm):
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
                instructor=user, is_active=True
            )
        else:
            self.fields['course'].queryset = Course.objects.filter(is_active=True)


class StudyMaterialUploadForm(forms.Form):
    ACCEPTED_EXTENSIONS = {'.pdf', '.docx', '.pptx', '.ppt', '.txt'}

    english_content = forms.CharField(
        widget=forms.Textarea(attrs={
            'rows': 14,
            'placeholder': 'Paste the course content or study notes in English here…',
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
            'placeholder': 'Optional: paste the Malayalam version here…',
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
        help_text='PDF, Word (.docx), PowerPoint (.pptx), or plain text (.txt). Overwrites pasted text above.',
        widget=forms.ClearableFileInput(attrs={'accept': '.pdf,.docx,.pptx,.ppt,.txt'}),
    )

    def clean_material_file(self):
        f = self.cleaned_data.get('material_file')
        if f:
            ext = '.' + f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else ''
            if ext not in self.ACCEPTED_EXTENSIONS:
                raise forms.ValidationError(
                    f'Unsupported file type "{ext}". Accepted: PDF, DOCX, PPTX, TXT.'
                )
        return f

    def clean(self):
        cleaned = super().clean()
        has_text = bool(cleaned.get('english_content', '').strip())
        has_file = bool(cleaned.get('material_file'))
        if not has_text and not has_file:
            raise forms.ValidationError(
                'Provide content either by pasting text or uploading a file.'
            )
        return cleaned
