from django.db import models
import uuid
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db.models import JSONField
from phonenumber_field.modelfields import PhoneNumberField
import django.utils.timezone
from datetime import datetime
from datetime import timezone
from datetime import timedelta
from os import environ
from django_celery_results.models import TaskResult

class User(AbstractUser):
    list_display = ['is_active', 'username', 'is_superuser',
                    'is_staff', 'first_name', 'last_name',
                    'email', 'date_joined' ]


class Membership(models.Model):
    id = models.UUIDField(default=uuid.uuid4,
                          unique=True,
                          primary_key=True,
                          editable=False)
    user = models.ForeignKey('User',
                             null=True,
                             blank=True,
                             on_delete=models.SET_NULL,
                             editable=False,
                             related_name='fk_user')
    org = models.ForeignKey('OrgAccount',
                            on_delete=models.SET_NULL,
                            null=True,
                            blank=True,
                            editable=False,
                            related_name='fk_org')
    active = models.BooleanField(default=False, editable=True)
    creation_date = models.DateTimeField(auto_now_add=True)
    modified_date = models.DateTimeField(auto_now=True)
    delete_requested = models.BooleanField(default=False, editable=True)
    # print("created: ",creation_date)
    activation_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):  # TBD add validators
        if self.org is not None:
            return str(self.org.name.replace(" ", "_") + ':' + self.user.username)
        else:
            return ':' + self.user.username

class Budget(models.Model):
    DEF_MAX_ALLOWANCE = 10000.0
    DEF_MONTHLY_ALLOWANCE = 1000.0
    DEF_BALANCE = 10000.0
    id = models.UUIDField(default=uuid.uuid4,
                          unique=True,
                          primary_key=True,
                          editable=False)
    content_type = models.ForeignKey(ContentType,on_delete=models.CASCADE)
    object_id = models.UUIDField()
    content_object = GenericForeignKey('content_type', 'object_id')
    creation_date = models.DateTimeField(auto_now_add=True)
    modified_date = models.DateTimeField(auto_now=True)

    max_allowance = models.DecimalField(max_digits=8, decimal_places=2, default=0.0)
    monthly_allowance = models.DecimalField(max_digits=8, decimal_places=2, default=0.0)
    balance = models.DecimalField(max_digits=8, decimal_places=2, default=0.0)
    fytd_accrued_cost = models.DecimalField(max_digits=8, decimal_places=2, default=0.0)
    most_recent_charge_time = models.DateTimeField(editable=True,default=django.utils.timezone.now)  # from Cost Explorer
    most_recent_credit_time = models.DateTimeField(editable=True,default=django.utils.timezone.now)  # from utils.reconcile
    most_recent_recon_time = models.DateTimeField(editable=True,default=django.utils.timezone.now)  # from utils.reconcile
    max_hrly = models.FloatField(default=0.0000001)
    cur_hrly = models.FloatField(default=0.0000001)
    min_hrly = models.FloatField(default=0.0000001)
    fc_min_hourly = models.JSONField(default=dict)  # current forecast
    fc_min_daily = models.JSONField(default=dict)  # current forecast
    fc_min_monthly = models.JSONField(default=dict)  # current forecast
    fc_cur_hourly = models.JSONField(default=dict)  # current forecast
    fc_cur_daily = models.JSONField(default=dict)  # current forecast
    fc_cur_monthly = models.JSONField(default=dict)  # current forecast
    fc_max_hourly = models.JSONField(default=dict)  # current forecast
    fc_max_daily = models.JSONField(default=dict)  # current forecast
    fc_max_monthly = models.JSONField(default=dict)  # current forecast

class ASGNodeLimits(models.Model):
    '''
    This is a wrapper for the Auto Scaling Group node limits
    '''
    # Validators limits:
    MIN_NODES = 0
    DEF_ADMIN_MAX_NODES = 10 # overrideable up to ABS_MAX_NODES
    ABS_MAX_NODES = 1000 # can be changed via ecs task template

    content_type = models.ForeignKey(ContentType,on_delete=models.CASCADE)
    object_id = models.UUIDField(default=uuid.uuid4,
                          unique=True,
                          primary_key=True,
                          editable=False)
    content_object = GenericForeignKey('content_type', 'object_id')
    num = models.IntegerField(  editable=True,  # this field can be edited in admin panel by user with admin privileges
                                default=MIN_NODES,
                                validators=[
                                    MinValueValidator(MIN_NODES),
                                    MaxValueValidator(ABS_MAX_NODES)])
    min = models.IntegerField(  editable=True,   # this field can be edited in admin panel by user with admin privileges
                                default=MIN_NODES,
                                validators=[
                                    MinValueValidator(MIN_NODES),
                                    MaxValueValidator(ABS_MAX_NODES)])
    max = models.IntegerField(  editable=True,   # this field is actual cluster state
                                default=DEF_ADMIN_MAX_NODES,
                                validators=[
                                    MinValueValidator(MIN_NODES),
                                    MaxValueValidator(ABS_MAX_NODES)])

class OrgAccount(models.Model):
    def __str__(self):
        return str(self.name)

    id = models.UUIDField(default=uuid.uuid4,
                          unique=True,
                          primary_key=True,
                          editable=False)
    owner = models.ForeignKey('User',
                              on_delete=models.CASCADE,
                              null=True,
                              blank=False)
    name = models.CharField(max_length=200,
                            default='uninitialized',
                            blank=False,
                            null=False)
    point_of_contact_name = models.CharField(max_length=200,
                                             default='support team',
                                             blank=False,
                                             null=False)
    email = models.EmailField(default='support@mail.slideruleearth.io')
    mfa_code = models.CharField(editable=True,
                                        max_length=16, 
                                        blank=False,
                                        null=False,
                                        default="sliderule_1492") # mfa-code entered
    budget = GenericRelation(Budget, related_query_name='org_budget', content_type_field='content_type', object_id_field='object_id', unique=True)
    # this is a summation of all the clusters auto scaling group node limits
    sum_asg = GenericRelation(ASGNodeLimits, related_query_name='sum_asg', content_type_field='content_type', object_id_field='object_id', unique=True)
    creation_date = models.DateTimeField(auto_now_add=True)
    modified_date = models.DateTimeField(auto_now=True)

class NodeGroupType(models.Model): 
    def __str__(self):
        return str(self.name)

    id = models.UUIDField(default=uuid.uuid4,
                          unique=True,
                          primary_key=True,
                          editable=False)
    node_mgnt_fixed_cost = models.FloatField(editable=True,default=0.145,help_text="https://aws.amazon.com/ec2/pricing/on-demand/ for monitor and ilb")  # Overhead==> (monitor is c7g.large, ilb is c7g.large; .0725)
    per_node_cost_per_hr = models.FloatField(default=0.2016,help_text="https://aws.amazon.com/ec2/pricing/on-demand/ for node")  # Per Node (r6g.xlarge = 0.226)
    name = models.CharField(max_length=128,
                            default='EC2-c7g.large-t4g.2xlarge',
                            blank=False,
                            null=False)
    description = models.CharField(max_length=500,default="Add Description here...")    # like An EC2 instance using c7g.large for the ilb and monitor and t4g.2xlarge for the nodes

class NodeGroup(models.Model): 
    def __str__(self):
        return str(self.org.name) + "-" + str(self.name)

    id = models.UUIDField(default=uuid.uuid4,
                          unique=True,
                          primary_key=True,
                          editable=False)

    name = models.CharField(max_length=200,
                            default='compute',
                            blank=False,
                            null=False)
    org = models.ForeignKey(OrgAccount, 
                            on_delete=models.CASCADE,
                            null=False,
                            blank=False)
    type = models.ForeignKey(NodeGroupType, on_delete=models.CASCADE, related_name="node_groups", null=True)    
    creation_date = models.DateTimeField(auto_now_add=True)
    modified_date = models.DateTimeField(auto_now=True)
    mgr_ip_address = models.GenericIPAddressField(default='0.0.0.0', editable=True)
    active_ps_cmd = models.CharField(max_length=32,
                                     default='',
                                     blank=True,
                                     null=False,
                                     editable=True)
    is_deployed = models.BooleanField(editable=True,
                                      default=False)
    deployed_state = models.CharField(max_length=64,
                                      default='unknown',
                                      blank=False,
                                      null=False,
                                      editable=True)

    cnnro_ids = JSONField(null=True, blank=True)  # This will store a list of UUIDs
    cur_version = models.CharField( editable=True,   # includes the v if is a release like 'v1.4.1'
                                    max_length=16, 
                                    blank=True,
                                    null=True) # current version of sw terraform files
    is_public = models.BooleanField(editable=True,default=False)
    expire_time = models.DateTimeField(editable=True,blank=True,null=True)
    provision_env_ready = models.BooleanField(editable=True,default=False)
    prov_env_version = models.CharField(editable=True,   # includes the v if is a release like 'v1.4.1'
                                        max_length=16, 
                                        blank=True,
                                        null=True) # current version of sw terraform files
    prov_env_is_public = models.BooleanField(editable=True,default=False)
    creation_date = models.DateTimeField(auto_now_add=True)
    modified_date = models.DateTimeField(auto_now=True)

    cur_asg = GenericRelation(ASGNodeLimits, related_query_name='cluster_cur_asg', content_type_field='content_type', object_id_field='object_id', unique=True)
    cfg_asg = GenericRelation(ASGNodeLimits, related_query_name='cluster_cfg_asg', content_type_field='content_type', object_id_field='object_id', unique=True, editable=True)
    budget = GenericRelation(Budget, related_query_name='cluster_budget', content_type_field='content_type', object_id_field='object_id', unique=True)


    ## these were migrated from v3 OrgAccount
    min_ddt = models.DateTimeField(editable=True,default=django.utils.timezone.now)
    cur_ddt = models.DateTimeField(editable=True,default=django.utils.timezone.now)
    max_ddt = models.DateTimeField(editable=True,default=django.utils.timezone.now)
    version = models.CharField( editable=True,   # includes the v if is a release like 'v1.4.1'
                                max_length=16, 
                                blank=False,
                                null=False,
                                default="latest") # version of terraform files
    # this is an editable limit using admin privileges
    admin_max_node_cap = models.IntegerField(editable=True,   # this field can be edited in admin panel by user with admin privileges
                                                default=ASGNodeLimits.DEF_ADMIN_MAX_NODES,
                                                validators=[
                                                    MinValueValidator(ASGNodeLimits.MIN_NODES),
                                                    MaxValueValidator(ASGNodeLimits.ABS_MAX_NODES)])
    time_to_live_in_mins = models.IntegerField(editable=True,default=60,validators=[MinValueValidator(15)]) 
    allow_deploy_by_token = models.BooleanField(default=True)
    destroy_when_no_nodes = models.BooleanField(default=True)
    is_public = models.BooleanField(editable=True,default=False)
    pcqr_display_age_in_hours = models.IntegerField(editable=True, default=72)
    pcqr_retention_age_in_days = models.IntegerField(editable=True, default=14)
    loop_count = models.BigIntegerField(editable=True,default=0)
    num_owner_ps_cmd = models.BigIntegerField(editable=True,default=0)
    num_ps_cmd = models.BigIntegerField(editable=True,default=0)
    num_ps_cmd_successful = models.BigIntegerField(editable=True,default=0)
    num_onn = models.BigIntegerField(editable=True,default=0)
    provisioning_suspended = models.BooleanField(editable=True,default=False)
    num_setup_cmd = models.BigIntegerField(editable=True,default=0)
    num_setup_cmd_successful = models.BigIntegerField(editable=True,default=0)



# use Migration to create a static table with one entry per choice for GranChoice (see REAMDME)

class GranChoice(models.Model):
    HOUR = 'HOURLY'  # NOTE these match ps_server enum
    DAY = 'DAILY'
    MONTH = 'MONTHLY'
    GRAN_CHOICES = [(HOUR, 'Hourly'), (DAY, 'Daily'), (MONTH, 'Monthly')]
    granularity = models.CharField(
        max_length=7, choices=GRAN_CHOICES, default=HOUR, primary_key=True)


class Cost(models.Model):  # Wrapper for Cost Explorer API
    id = models.UUIDField(default=uuid.uuid4,
                          unique=True,
                          primary_key=True,
                          editable=False)
    content_type = models.ForeignKey(ContentType,on_delete=models.CASCADE)
    object_id = models.UUIDField()
    content_object = GenericForeignKey('content_type', 'object_id')
    creation_date = models.DateTimeField(auto_now_add=True)
    modified_date = models.DateTimeField(auto_now=True)
    gran = models.ForeignKey(GranChoice, on_delete=models.PROTECT,
                             null=True,
                             blank=True,
                             editable=False,
                             related_name='fk_gran')
    tm = models.DateTimeField(editable=True,default=django.utils.timezone.now)
    cnt = models.IntegerField(default=0)
    avg = models.FloatField(default=0.0)
    min = models.FloatField(default=0.0)
    max = models.FloatField(default=0.0)
    std = models.FloatField(default=0.0)
    ccr = models.JSONField(default=dict)  # current cost report
    # last time we got hr/day/month cost reports for this org
    # from the aws cost explorer (minimize # calls to 3 a day per
    cost_refresh_time = models.DateTimeField(editable=True,default=django.utils.timezone.now)

class ClusterNumNode(models.Model):
    id = models.UUIDField(default=uuid.uuid4,
                          unique=True,
                          primary_key=True,
                          editable=False)
    user = models.ForeignKey('User',
                             null=True,
                             blank=True,
                             on_delete=models.CASCADE,
                             editable=False,
                             related_name='fk_user_cnn')
    cluster = models.ForeignKey('NodeGroup',
                                on_delete=models.CASCADE,
                                null=True,
                                blank=True,
                                editable=False,
                                related_name='fk_cluster_cnn')
    desired_num_nodes = models.IntegerField(editable=True,  # this field can be edited in admin panel by user with admin privileges
                                            default=ASGNodeLimits.MIN_NODES,
                                            validators=[
                                                MinValueValidator(ASGNodeLimits.MIN_NODES),
                                                MaxValueValidator(ASGNodeLimits.ABS_MAX_NODES)])
    expiration = models.DateTimeField(editable=True,null=True,blank=True)
    has_active_ps_cmd = models.BooleanField(default=False)

class PsCmdResult(models.Model):
    id = models.UUIDField(default=uuid.uuid4,
                          unique=True,
                          primary_key=True,
                          editable=False)
    cluster = models.ForeignKey('NodeGroup',
                                null=True,
                                on_delete=models.CASCADE,
                                blank=True,
                                editable=False,
                                related_name='fk_cluster_cr')
    ps_cmd_output = models.TextField(   help_text='Output from the provisioning system server',
                                        default='',
                                        editable=False,
                                        verbose_name='Provision Cmd Results')
    error = models.TextField(   help_text='Errors/Warnings processing cmd',
                                default='',
                                editable=False,
                                verbose_name='Provision Server Errors')
    creation_date = models.DateTimeField(auto_now_add=True)
    ps_cmd_summary_label = models.TextField(help_text='ps cmd task output',
                                            default='',
                                            editable=False,
                                            verbose_name='Provision Cmd')
    expiration = models.DateTimeField(editable=False,blank=True,null=True,default=django.utils.timezone.now)
   
class OwnerPSCmd(models.Model):
    id = models.UUIDField(default=uuid.uuid4,
                          unique=True,
                          primary_key=True,
                          editable=False)
    user = models.ForeignKey('User',
                             null=True,
                             blank=True,
                             on_delete=models.CASCADE,
                             editable=False,
                             related_name='fk_user_opscmd')
    cluster = models.ForeignKey('NodeGroup',
                                on_delete=models.CASCADE,
                                null=True,
                                blank=True,
                                editable=False,
                                related_name='fk_cluster_opscmd')
    ps_cmd = models.CharField(  max_length=32,
                                default='',
                                blank=True,
                                null=True,
                                editable=False)
    deploy_values = models.JSONField(default=dict,null=True,blank=True)
    create_time = models.DateTimeField(editable=True,blank=True,null=True)
    
