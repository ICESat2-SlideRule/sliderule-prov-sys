from typing import Any
from django import forms
from django.forms import ModelForm
from django.contrib.auth.forms import UserCreationForm, UsernameField, UserChangeForm
from django.forms import BaseInlineFormSet
from django.forms import modelformset_factory
from django.forms import ModelForm, IntegerField, DecimalField
from django.forms import BaseModelFormSet

from django.contrib.contenttypes.forms import generic_inlineformset_factory
from .models import Membership, OrgAccount, NodeGroup, NodeGroupType, User, ClusterNumNode, ASGNodeLimits, Budget, Cost
from captcha.fields import CaptchaField
from datetime import datetime,timedelta
from datetime import timezone
from django.contrib.auth import get_user_model
from django.urls import reverse
from .global_constants import MIN_TTL, MAX_TTL
from .tasks import getGranChoice,init_new_org_memberships
from allauth.account.forms import LoginForm
from allauth.account.forms import SignupForm
from allauth.socialaccount.forms import SignupForm as SocialSignupForm
from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML
from crispy_forms.layout import Layout, Div, Row, Column, Field
from django.core.exceptions import ValidationError
from django.forms.widgets import NumberInput
from django.forms import ModelForm, NumberInput, TextInput, CheckboxInput, Widget
from django.db import transaction


import logging
LOG = logging.getLogger('django')

def has_non_blank_char(s):
    return any(c.strip() for c in s)
def has_at_least_three_alpha(s):
    return sum(1 for c in s if c.isalpha()) >= 3


class ExampleForm(forms.Form):
    name = forms.CharField()
    email = forms.EmailField()

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


class NodeGroupTypeCreateForm(ModelForm):
    '''
        For creating a new Node Group Type
    '''
    class Meta:
        model = NodeGroupType
        fields = ['name', 'definition', 'description', 'node_mgnt_fixed_cost', 'per_node_cost_per_hr']
        readonly = ['id']

NodeGroupTypeCreateFormSet = modelformset_factory(
    model=NodeGroupType, 
    form=NodeGroupTypeCreateForm, 
    extra=1, # You can adjust this number if you need to display more than one empty form for adding new NodeGroups
    fields = ['name', 'definition', 'description', 'node_mgnt_fixed_cost', 'per_node_cost_per_hr'],

    can_delete=True,  # If you want to allow deleting node groups through the formset
)

def create_related_costs(instance, init_accounting_tm):
    try:
        for granularity in ["HOURLY", "DAILY", "MONTHLY"]:
            has_Cost = Cost.objects.filter(object_id=instance.id, gran=granularity).exists()
            if not has_Cost:
                granObj = getGranChoice(granularity=granularity)
                Cost.objects.create(content_object=instance, gran=granObj, tm=init_accounting_tm, cost_refresh_time=init_accounting_tm)
    except Exception as e:
        LOG.error(f"create_related_costs Exception:{e}")

class NodeGroupCreateForm(ModelForm):
    '''
        For creating a new Node Group
    '''
    cfg_asg_min = IntegerField(label="Min", initial=ASGNodeLimits.MIN_NODES)
    cfg_asg_max = IntegerField(label="Max", initial=ASGNodeLimits.DEF_ADMIN_MAX_NODES)
    budget_max_allowance = DecimalField(max_digits=8, decimal_places=2, label="Budget Max Allowance", initial=Budget.DEF_MAX_ALLOWANCE)
    budget_monthly_allowance = DecimalField(max_digits=8, decimal_places=2, label="Budget Monthly Allowance", initial=Budget.DEF_MONTHLY_ALLOWANCE) 
    budget_balance = DecimalField(max_digits=8, decimal_places=2, label="Budget Balance", initial=Budget.DEF_BALANCE)

    def __init__(self, *args, **kwargs):
        self._org = kwargs.pop('org', None)
        #LOG.info(f"NodeGroupCreateForm.__init__ org:{self._org}")
        super(NodeGroupCreateForm, self).__init__(*args, **kwargs)

    class Meta:
        model = NodeGroup
        fields = ['name', 'type', 'budget_balance', 'budget_max_allowance', 'budget_monthly_allowance', 'cfg_asg_min', 'cfg_asg_max']

    def save(self, commit=True):
        with transaction.atomic():
            init_accounting_tm = datetime.now(timezone.utc)-timedelta(days=366) # force update of accounting
            instance = super().save(commit=False)           
            instance.org = self._org   # Set the org to the specific instance passed during form initialization
            LOG.info(f"NodeGroupCreateForm.save org:{instance.org}")
            if commit:
                instance.save()
                
            # Create the related ASGNodeLimits instance
            try:
                has_ASGNodeLimits = ASGNodeLimits.objects.filter(object_id=instance.id).exists()
                if not has_ASGNodeLimits:
                    asg_limits = ASGNodeLimits(
                        min=self.cleaned_data['cfg_asg_min'],
                        max=self.cleaned_data['cfg_asg_max'],
                        content_object=instance
                    )
                    asg_limits.save()
                else:
                    asg_limits = ASGNodeLimits.objects.get(object_id=instance.id)
                    asg_limits.min=self.cleaned_data['cfg_asg_min']
                    asg_limits.max=self.cleaned_data['cfg_asg_max']
                    asg_limits.save()
            except Exception as e:
                LOG.error(f"save ASGNodeLimits Exception:{e}")
            # Create the related Budget instance
            try:
                has_Budget = Budget.objects.filter(object_id=instance.id).exists()
                if not has_Budget:
                    budget = Budget(
                        max_allowance=self.cleaned_data['budget_max_allowance'],
                        monthly_allowance=self.cleaned_data['budget_monthly_allowance'],
                        balance=self.cleaned_data['budget_balance'],
                        most_recent_charge_time=init_accounting_tm,# force update of accounting
                        most_recent_credit_time=init_accounting_tm,# force update of accounting
                        content_object=instance
                    )
                    budget.save()
                else:
                    budget = Budget.objects.get(object_id=instance.id)
                    budget.max_allowance=self.cleaned_data['budget_max_allowance']
                    budget.monthly_allowance=self.cleaned_data['budget_monthly_allowance']
                    budget.balance=self.cleaned_data['budget_balance']
                    budget.most_recent_charge_time=init_accounting_tm
            except Exception as e:
                LOG.error(f"save Budget Exception:{e}")
            create_related_costs(instance, init_accounting_tm)
        return instance

class BaseNodeGroupCreateFormSet(BaseModelFormSet):

    def __init__(self, *args, **kwargs):
        self.org = kwargs.pop('org', None)
        #LOG.info(f"BaseNodeGroupCreateFormSet.__init__ org:{self.org}")
        super(BaseNodeGroupCreateFormSet, self).__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        #LOG.info(f"BaseNodeGroupCreateFormSet.get_form_kwargs self.org:{self.org} kwargs:{kwargs}")
        if self.org:
            kwargs['org'] = self.org
        #LOG.info(f"BaseNodeGroupCreateFormSet.get_form_kwargs kwargs:{kwargs}")
        return kwargs

NodeGroupCreateFormSet = modelformset_factory(NodeGroup, form=NodeGroupCreateForm, formset=BaseNodeGroupCreateFormSet, extra=1)

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

class NodeGroupCfgForm(ModelForm):
    '''
    For configuring an existing Node Group
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
        fields = ['version', 'is_public', 'allow_deploy_by_token', 'destroy_when_no_nodes','provisioning_suspended' ]

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if name == 'uninitialized':
            raise ValidationError('The name "uninitialized" is not allowed.')
        if not has_non_blank_char(name):
            raise ValidationError('The name must contain at least one non-blank character.')
        if not has_at_least_three_alpha(name):
            raise ValidationError('The name must contain at least three alphabetic characters.')
        return name


    def save(self, commit=True):
        with transaction.atomic():
            instance = super().save(commit=False)
            
            # assuming you're using the instance right after saving
            if commit:
                instance.save()

            try:    
                # Create the related ASGNodeLimits instance
                asg_limits = ASGNodeLimits(
                    num=self.cleaned_data['cfg_asg_num'],
                    min=self.cleaned_data['cfg_asg_min'],
                    max=self.cleaned_data['cfg_asg_max'],
                    content_object=instance
                )
                asg_limits.save()
            except Exception as e:
                LOG.error(f"save ASGNodeLimits Exception:{e}")
        
            try:
                # Create the related Budget instance
                budget = Budget(
                    max_allowance=self.cleaned_data['budget_max_allowance'],
                    monthly_allowance=self.cleaned_data['budget_monthly_allowance'],
                    balance=self.cleaned_data['budget_balance'],
                    content_object=instance
                )
                budget.save()
            except Exception as e:
                LOG.error(f"save Budget Exception:{e}")
        return instance

class ReadOnlyBudgetForm(forms.ModelForm):
    class Meta:
        model = Budget
        fields = ['balance', 'max_allowance', 'monthly_allowance']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['balance'].widget.attrs['readonly'] = True
        self.fields['max_allowance'].widget.attrs['readonly'] = True
        self.fields['monthly_allowance'].widget.attrs['readonly'] = True

class OrgBudgetForm(forms.ModelForm):
    max_allowance = DecimalField(max_digits=8, decimal_places=2, label="Budget Max Allowance", initial=Budget.DEF_MAX_ALLOWANCE)
    monthly_allowance = DecimalField(max_digits=8, decimal_places=2, label="Budget Monthly Allowance", initial=Budget.DEF_MONTHLY_ALLOWANCE) 
    balance = DecimalField(max_digits=8, decimal_places=2, label="Budget Balance", initial=Budget.DEF_BALANCE)

    def __init__(self, *args, **kwargs):
        self._org = kwargs.pop('org', None)
        super().__init__(*args, **kwargs)
        self.fields['balance'].widget.attrs['readonly'] = True
        self.fields['max_allowance'].widget.attrs['readonly'] = True
        self.fields['monthly_allowance'].widget.attrs['readonly'] = True

    class Meta:
        model = Budget
        fields = ['balance', 'max_allowance', 'monthly_allowance']

    def add_to_field(self,field_name ,amount):
        self.fields[f'{field_name}'] += amount

    def save(self, commit=True):
        instance.org = self._org
        instance = super().save(commit=False)
        return instance

class OrgCreateForm(ModelForm):
    budget_max_allowance = DecimalField(label="Budget Max Allowance", initial=Budget.DEF_MAX_ALLOWANCE)
    budget_monthly_allowance = DecimalField(label="Budget Monthly Allowance", initial=Budget.DEF_MONTHLY_ALLOWANCE) 
    budget_balance = DecimalField(label="Budget Balance", initial=Budget.DEF_BALANCE)
    class Meta:
        model = OrgAccount
        fields = ['owner', 'name', 'point_of_contact_name', 'email', 'mfa_code', 'budget_balance', 'budget_max_allowance', 'budget_monthly_allowance']

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if name == 'uninitialized':
            raise ValidationError('The name "uninitialized" is not allowed.')
        if not has_non_blank_char(name):
            raise ValidationError('The name must contain at least one non-blank character.')
        if not has_at_least_three_alpha(name):
            raise ValidationError('The name must contain at least three alphabetic characters.')
        return name

    def save(self, commit=True):
        with transaction.atomic():
            instance = super().save(commit=False)
            
            if commit:
                instance.save()
                
            # Create the related ASGNodeLimits instance for sum_asg
            sum_asg = ASGNodeLimits(
                num=0,
                min=0,
                max=0,
                content_object=instance
            )
            sum_asg.save()
            init_accounting_tm = datetime.now(timezone.utc)-timedelta(days=366) # force update of accounting
            # Create the related Budget instance
            try:
                budget = Budget(
                    max_allowance=self.cleaned_data['budget_max_allowance'],
                    monthly_allowance=self.cleaned_data['budget_monthly_allowance'],
                    balance=self.cleaned_data['budget_balance'],
                    most_recent_charge_time=init_accounting_tm,# force update of accounting
                    most_recent_credit_time=init_accounting_tm,# force update of accounting
                    content_object=instance
                )
                budget.save()
            except Exception as e:
                LOG.error(f"save Budget Exception:{e}")
            create_related_costs(instance, init_accounting_tm)
            init_new_org_memberships(instance)
        return instance

class OrgProfileForm(ModelForm):
    class Meta:
        model = OrgAccount
        fields = ['point_of_contact_name', 'email', ]

class CustomLoginForm(LoginForm):
    def __init__(self, *args, **kwargs):
        super(CustomLoginForm, self).__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_class = 'form-horizontal w-100'
        self.helper.label_class = 'col-lg-2 w-100'
        self.helper.field_class = 'col-lg-8 w-100'
