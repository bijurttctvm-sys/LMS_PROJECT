from django import forms

from users.models import User
from .models import Batch, Course, EnrollmentRequest


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ('title', 'description', 'instructor', 'language', 'is_active')

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and user.role == User.Role.INSTRUCTOR:
            self.fields.pop('instructor', None)
        else:
            self.fields['instructor'].queryset = User.objects.filter(
                role=User.Role.INSTRUCTOR
            )
            self.fields['instructor'].required = False
            self.fields['instructor'].label = 'Trainer'


class BatchForm(forms.ModelForm):
    class Meta:
        model = Batch
        fields = ('name', 'description')


class EnrollmentRequestForm(forms.ModelForm):
    class Meta:
        model = EnrollmentRequest
        fields = ('request_reason',)
        widgets = {
            'request_reason': forms.Textarea(
                attrs={
                    'rows': 4,
                    'placeholder': 'Tell the admin why you need access to this course.',
                }
            ),
        }
        labels = {
            'request_reason': 'Reason for requesting course access',
        }
        help_texts = {
            'request_reason': 'This note is shown to the admin when they review your request.',
        }

    def clean_request_reason(self):
        value = (self.cleaned_data.get('request_reason') or '').strip()
        if not value:
            raise forms.ValidationError('Please provide a reason for requesting access.')
        return value
