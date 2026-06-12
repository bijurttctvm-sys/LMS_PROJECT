from django import forms

from users.models import User
from .models import Course


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
