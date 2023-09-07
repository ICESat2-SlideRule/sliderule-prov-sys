from django import forms
from django.forms import ModelForm
from django.contrib.auth.forms import UserCreationForm, UsernameField, UserChangeForm
from django.forms import BaseInlineFormSet
from django.contrib.contenttypes.forms import BaseGenericInlineFormSet

from django.contrib.contenttypes.forms import generic_inlineformset_factory
from .models import Membership, OrgAccount, NodeGroup, User, ClusterNumNode, ASGNodeLimits, Budget
from captcha.fields import CaptchaField
from datetime import datetime,timedelta
from datetime import timezone
from django.contrib.auth import get_user_model
from django.urls import reverse
from .global_constants import MIN_TTL, MAX_TTL

from allauth.account.forms import LoginForm
from allauth.account.forms import SignupForm
from allauth.socialaccount.forms import SignupForm as SocialSignupForm
from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML
from crispy_forms.layout import Layout, Div, Row, Column, Field
from django.core.exceptions import ValidationError
from django.forms.widgets import NumberInput
from django.forms import ModelForm, NumberInput, TextInput, CheckboxInput, Widget


class CustomSignupForm(SignupForm):
    first_name = forms.CharField(max_length=30, label='First Name')
    last_name = forms.CharField(max_length=30, label='Last Name')
    username = forms.CharField(max_length=150, label='Username')
    captcha = CaptchaField()

    def save(self, request):
        user = super(CustomSignupForm, self).save(request)
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.username = self.cleaned_data['username']
        user.save()
        return user

class MyCustomSocialSignupForm(SocialSignupForm):
    first_name = forms.CharField(max_length=30, label='First Name')
    last_name = forms.CharField(max_length=30, label='Last Name')
    username = forms.CharField(max_length=150, label='Username')

    def save(self, request):
        # Ensure you call the parent class's save.
        # .save() returns a User object.
        user = super(MyCustomSocialSignupForm, self).save(request)

        # Add your own processing here.
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.username = self.cleaned_data['username']
        user.save()
        # You must return the original result.
        return user
    
class UserProfileForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ['username', 'first_name',
                  'last_name', 'email']
        field_classes = {'username': UsernameField}
        required = ['username']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class ClusterNumNodeForm(forms.ModelForm):
    ttl_minutes = forms.IntegerField(required=True, min_value=0, label='TTL minutes')

    class Meta:
        model = ClusterNumNode
        fields = ['desired_num_nodes']

    def __init__(self, *args, **kwargs):    
        self.min_nodes = kwargs.pop('min_nodes', None)
        self.max_nodes = kwargs.pop('max_nodes', None)
        super().__init__(*args, **kwargs)
        # self.fields['desired_num_nodes'].validators.append(desired_num_nodes_validator(self.min_nodes, self.max_nodes))
        if self.min_nodes == 0:
            self.fields['desired_num_nodes'].initial = 1
        else:
            self.fields['desired_num_nodes'].initial = self.min_nodes
        self.fields['ttl_minutes'].initial = 15

    def clean_ttl_minutes(self):
        ttl_minutes = self.cleaned_data['ttl_minutes']
        if ttl_minutes < MIN_TTL:
            ttl_minutes = MIN_TTL
        if ttl_minutes > MAX_TTL:
            ttl_minutes = MAX_TTL
        return ttl_minutes


class MembershipForm(forms.Form):
    username = forms.CharField(disabled=True)
    firstname = forms.CharField(disabled=True)
    lastname = forms.CharField(disabled=True)
    active = forms.BooleanField(required=False)
    delete = forms.BooleanField(required=False)

class BudgetForm(forms.ModelForm):
    class Meta:
        model = Budget
        fields = ['max_allowance', 'monthly_allowance', 'balance']

class ClusterForm(ModelForm):
    '''
        For creating a new cluster
    '''
    class Meta:
        model = NodeGroup
        fields = [ 'name', 'org', 'node_mgr_fixed_cost', 'node_fixed_cost']
        readonly_fields = ['org']

class ASGNodeLimitsForm(ModelForm):
    '''
        For creating an Auto Scaling Group node limits object
    '''
    def __init__(self, *args, **kwargs):
        max_value = NodeGroup.ABS_MAX_NODES
        width = len(str(max_value))
        self.fields['min_node_cap'].widget = NumberInput(attrs={'style': f'width: {width}em'})
        self.fields['max_node_cap'].widget = NumberInput(attrs={'style': f'width: {width}em'})
        super().__init__(*args, **kwargs)

    class Meta:
        model = ASGNodeLimits
        fields = ['min', 'num', 'max']

class ClusterCfgForm(ModelForm):
    '''
    For configuring an existing cluster
    '''
    version = forms.ChoiceField(widget=forms.Select(attrs={'id': 'version'}))
    is_public = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={'id': 'is_public'}))
    def __init__(self, *args, **kwargs):
        available_versions = kwargs.pop('available_versions', None)
        super().__init__(*args, **kwargs)
        if available_versions:
            self.fields['version'].choices = [(v, v) for v in available_versions]
    class Meta:
        model = NodeGroup
        fields = ['provisioning_suspended','is_public', 'version', 'allow_deploy_by_token', 'destroy_when_no_nodes' ]
        readonly_fields = ['cfg_asg']

class OrgProfileForm(ModelForm):
    class Meta:
        model = OrgAccount
        fields = ['point_of_contact_name', 'email', ]

class OrgAccountForm(ModelForm):
    class Meta:
        model = OrgAccount
        fields = '__all__'


BudgetFormSet = generic_inlineformset_factory(Budget, extra=0, fields=['balance','max_allowance','monthly_allowance'],can_delete=False)

class CustomLoginForm(LoginForm):
    def __init__(self, *args, **kwargs):
        super(CustomLoginForm, self).__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_class = 'form-horizontal w-100'
        self.helper.label_class = 'col-lg-2 w-100'
        self.helper.field_class = 'col-lg-8 w-100'
