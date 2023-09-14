from __future__ import print_function
import ps_server_pb2_grpc
import ps_server_pb2
import grpc
import pytz
import ast
import json
import os
import redis
from django_celery_results import views as celery_views
from django.http import  HttpResponse
from datetime import timezone
from dateutil import tz
from django.urls import reverse
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, render, redirect, HttpResponseRedirect
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.db.transaction import get_autocommit
from .models import NodeGroup, NodeGroupType, GranChoice, OrgAccount, Cost, Membership, User, ClusterNumNode, PsCmdResult, OwnerPSCmd, Budget
from .forms import MembershipForm, NodeGroupCfgForm, NodeGroupCreateForm, OrgCreateForm, OrgProfileForm, UserProfileForm,ClusterNumNodeForm,OrgBudgetForm,NodeGroupCreateFormSet,NodeGroupTypeCreateFormSet,ReadOnlyBudgetForm
from .utils import get_db_cluster_cost,add_obj_cost,add_node_group_cost
from .tasks import get_versions, update_burn_rates, getGranChoice, sort_CNN_by_nn_exp,forever_loop_main_task,get_node_group_queue_name,remove_num_node_requests,get_PROVISIONING_DISABLED,set_PROVISIONING_DISABLED,process_num_nodes_api
from django.core.mail import send_mail
from django.conf import settings
from django.forms import formset_factory
from django.template.defaulttags import register
import logging
from django.http import JsonResponse
from django.db.models import Q
from django.utils.timezone import is_aware
import datetime
from google.protobuf.json_format import MessageToJson
from users import ps_client
from users.global_constants import *
from django.db import transaction
from datetime import datetime, timedelta
from .tasks import cost_accounting_org,cost_accounting_cluster, init_new_org_memberships
from django.contrib.auth import get_user_model
from allauth.account.decorators import verified_email_required
from django.contrib.sites.models import Site



# logging.basicConfig(
#     format='%(asctime)s %(levelname)-8s %(message)s',
#     level=logging.INFO,
#     datefmt='%Y-%m-%d %H:%M:%S')
LOG = logging.getLogger('django')

def get_user_orgs(request):
    active_user = request.user
    if(request.user.is_authenticated):
        user_orgs = OrgAccount.objects.filter(owner=active_user)
    else:
        user_orgs = {}
    PS_BLD_ENVVER = settings.PS_BLD_ENVVER
    version_is_release = PS_BLD_ENVVER.startswith('v') and ('-0-' in PS_BLD_ENVVER) and not ('-dirty' in PS_BLD_ENVVER)
    if '-0-' in PS_BLD_ENVVER:
        PS_BLD_ENVVER =  PS_BLD_ENVVER.rsplit('-')[0]
    domain = os.environ.get("DOMAIN")
    return{ "user_orgs": user_orgs, 
            "active_user": active_user, 
            "DEBUG": settings.DEBUG, 
            "GIT_VERSION":settings.GIT_VERSION, 
            "DOCKER_TAG":settings.DOCKER_TAG,
            "PS_VERSION":settings.PS_VERSION, 
            "PS_SITE_TITLE":settings.PS_SITE_TITLE, 
            "PS_BLD_ENVVER":PS_BLD_ENVVER,
            "version_is_release":version_is_release,
            "domain": domain }

def send_activation_email(request, orgname, user):
    domain = os.environ.get("DOMAIN")
    subject = f"Membership to {orgname}"
    message = f"{user.first_name} {user.last_name}, \nYour membership to {orgname} on https://{domain} has been activated.\nYou may use the system. To learn how to use the {orgname} cluster see the user guide: https://slideruleearth.io/web/rtd/"
    LOG.info("-----> sending email... %s", [user.email])
    from_email = f'support@mail.{domain}'

    try:
        send_mail(
            subject,
            message,
            from_email,
            [user.email],
            fail_silently=False,
        )
    except Exception as e:
        LOG.exception('Exception caught when sending activation email')
        messages.error(request, 'INTERNAL ERROR; FAILED to send activation email')

    
@login_required(login_url='account_login')
@verified_email_required
@transaction.atomic
def nodeGroupTypes(request):
    try:
        if request.method == "POST":
            nodeGroupTypeCreateFormSet = NodeGroupTypeCreateFormSet(request.POST, queryset=NodeGroupType.objects.all())
            if nodeGroupTypeCreateFormSet.is_valid():
                lenOfForms = len(list(nodeGroupTypeCreateFormSet))
                for form in nodeGroupTypeCreateFormSet:
                    if form.has_changed() or lenOfForms == 1:
                        if form.cleaned_data.get('DELETE'):
                            form.instance.delete()
                            LOG.info("form deleted")
                        else:
                            form.save()
                            LOG.info("form saved")
                        messages.success(request, "Node Group Types successfully updated")
                    else:
                        LOG.info("form has not changed")
                        messages.info(request, "Node Group Types NOT updated: no changes detected")
            else:
                # Display specific errors from the formset
                for form in nodeGroupTypeCreateFormSet:
                    for field, errors in form.errors.items():
                        for error in errors:
                            messages.error(request, f"Error in {field}: {error}")
                messages.error(request, "Node Group Types NOT updated")
        else:
            nodeGroupTypeCreateFormSet = NodeGroupTypeCreateFormSet(queryset=NodeGroupType.objects.all())
        context = {'nodeGroupTypeCreateFormSet': nodeGroupTypeCreateFormSet}
        return render(request, 'users/node_group_types.html', context)
    except Exception as e:
        LOG.exception("Caught unexpected exception")
        messages.error(request, f"Unexpected error: {repr(e)}")
    return redirect('browse')


@login_required(login_url='account_login')
@verified_email_required
def orgManageMembers(request, pk):
    nodeGroupObj = NodeGroup.objects.get(id=pk)
    orgAccountObj = nodeGroupObj.org
    LOG.info(f"{request.method} {orgAccountObj.name}")
    if request.user.groups.filter(name='PS_Developer').exists() or orgAccountObj.owner == request.user:
        memberships = Membership.objects.filter(org=orgAccountObj.id)
        formset_initial = []
        for m in memberships:
            tuple = {'username':m.user.username,'firstname': m.user.first_name,
                    'lastname': m.user.last_name, 'active': m.active}
            formset_initial.append(tuple)
        MembershipFormSet = formset_factory(MembershipForm, extra=0)
        if request.method == "POST":
            formset = MembershipFormSet(request.POST, initial=formset_initial)
            if formset.is_valid():
                emails_sent=False
                for form in formset:
                    user_set = get_user_model().objects.filter(first_name=form.cleaned_data.get(
                        'firstname')).filter(last_name=form.cleaned_data.get('lastname'))
                    for m in memberships:  # TBD is there a more efficient way?
                        old_active_state = m.active
                        if (m.user.first_name == user_set[0].first_name) and (m.user.last_name == user_set[0].last_name):
                            m.active = form.cleaned_data.get('active')
                            if(m.active and not old_active_state):
                                m.activation_date = datetime.now(timezone.utc)
                                LOG.info("Member:%s is now active @ %s",
                                        m.user.last_name, m.activation_date.strftime("%a %b %d %I:%M:%S %p %Z"))
                                # maybe add an email col to org_manage_members to give owner control of sending email or not?
                                send_activation_email(request,orgAccountObj.name,m.user)
                                emails_sent = True
                            else:
                                LOG.info("Member:%s NOT active", m.user.last_name)
                            m.save()  # need to save active AND is not active
                msg = f"{request.user.username} your Organization account:{orgAccountObj.name} was updated successully"
                if emails_sent:
                    msg += ", emails were sent to notify newly activated users"
                messages.success(request, msg)
                return redirect('browse')
        else:
            formset = MembershipFormSet(initial=formset_initial)
        context = {'org': orgAccountObj,
                'memberships': memberships, 'formset': formset}
        return render(request, 'users/manage_memberships.html', context)
    else:
        messages.error(request,"Unauthorized access")
        return HttpResponse('Unauthorized', status=401)

@login_required(login_url='account_login')
@verified_email_required
def nodeGroupManage(request, pk):
    #LOG.info("%s %s",request.method,pk)
    nodeGroupObj = NodeGroup.objects.get(id=pk)
    orgAccountObj = nodeGroupObj.org
    LOG.info(f"{request.method} {nodeGroupObj} loop_count:{nodeGroupObj.loop_count} ps:{nodeGroupObj.num_ps_cmd} ops:{nodeGroupObj.num_owner_ps_cmd} cnn:{nodeGroupObj.num_onn} autocommit:{get_autocommit()}")
    clusterNumNodeObjs = sort_CNN_by_nn_exp(nodeGroupObj)
    user_is_developer = request.user.groups.filter(name='PS_Developer').exists()
    if user_is_developer or orgAccountObj.owner == request.user:
        try:
            filter_time = (datetime.now(timezone.utc)-timedelta(hours=nodeGroupObj.pcqr_display_age_in_hours)).replace(microsecond=0)
            purge_time = (datetime.now(timezone.utc)-timedelta(days=nodeGroupObj.pcqr_retention_age_in_days)).replace(microsecond=0)
            LOG.debug(f"{nodeGroupObj} display filter_tm:{filter_time} current purge tm:{purge_time}")
            psCmdResultObjs = PsCmdResult.objects.filter(Q(expiration__gt=(filter_time)) | Q(expiration__isnull=True)).filter(cluster=nodeGroupObj).order_by('-creation_date')
        except PsCmdResult.DoesNotExist:
            psCmdResultObjs = None
        #LOG.info("%s is_deployed?:%s  deployed_state:%s", nodeGroupObj, nodeGroupObj.is_deployed,nodeGroupObj.deployed_state)
        update_burn_rates(nodeGroupObj) # updates nodeGroupObj.version nodeGroupObj.cur_asg.num

        if request.method == "POST":
            form_submit_value = request.POST.get('form_submit')
            LOG.info(f"form_submit_value:{form_submit_value}")
            if form_submit_value == 'add_onn':
                add_cnn_form = ClusterNumNodeForm(request.POST,min_nodes=nodeGroupObj.cfg_asg.min,max_nodes=nodeGroupObj.cfg_asg.max, prefix = 'add_onn')
                msg = ''
                if (add_cnn_form.is_valid() and (int(add_cnn_form.data['add_onn-desired_num_nodes']) >= 0)):
                    desired_num_nodes = add_cnn_form.cleaned_data['desired_num_nodes']
                    LOG.info(f"desired_num_nodes:{desired_num_nodes}")
                    ttl_minutes = add_cnn_form.cleaned_data['ttl_minutes']
                    if ttl_minutes != int(add_cnn_form.data['add_onn-ttl_minutes']):
                        msg = f"Clamped ttl_minutes! - "
                    expire_time = datetime.now(timezone.utc)+timedelta(minutes=ttl_minutes)
                    jrsp,status = process_num_nodes_api(name=orgAccountObj.name, cluster_name=nodeGroupObj.name, user=request.user, desired_num_nodes=desired_num_nodes, expire_time=expire_time)
                    if status == 200:
                        msg += jrsp['msg']
                        messages.success(request,msg)
                    else:
                        messages.error(request,jrsp['error_msg'])
                else:
                    emsg = f"Input Errors:{add_cnn_form.errors.as_text}"
                    messages.error(request, emsg)
                    LOG.info(f"Did not create CNN for {nodeGroupObj} {emsg}")
            else:
                add_cnn_form = ClusterNumNodeForm(min_nodes=nodeGroupObj.cfg_asg.min,max_nodes=OrgAccount.admin_max_node_cap,prefix = 'add_onn')
        else:
            add_cnn_form = ClusterNumNodeForm(min_nodes=nodeGroupObj.cfg_asg.min,max_nodes=OrgAccount.admin_max_node_cap,prefix = 'add_onn')
        LOG.info(f"{nodeGroupObj} cluster current_version:{nodeGroupObj.cur_version} provision_env_ready:{nodeGroupObj.provision_env_ready}")
        #LOG.info(f"about to get versions")
        versions = get_versions()
        config_form = NodeGroupCfgForm(instance=nodeGroupObj,available_versions=versions)

        domain = os.environ.get("DOMAIN")
        pending_refresh = None
        pending_destroy = None
        try:
            OwnerPSCmd.objects.get(cluster=nodeGroupObj, ps_cmd='Refresh')
            pending_refresh = True
        except OwnerPSCmd.DoesNotExist:
            pending_refresh = False
        try:
            OwnerPSCmd.objects.get(cluster=nodeGroupObj, ps_cmd='Destroy')
            pending_destroy = True
        except OwnerPSCmd.DoesNotExist:
            pending_destroy = False
        context = {'org': orgAccountObj, 'cluster': nodeGroupObj, 'add_cnn_form': add_cnn_form,
                'config_form': config_form, 'ps_cmd_rslt_objs':psCmdResultObjs,
                'cluster_mod_date_utc': nodeGroupObj.modified_date.replace(tzinfo=pytz.utc),
                'cnn_objs':clusterNumNodeObjs,
                'domain':domain, 'user_is_developer':user_is_developer, 'now':datetime.now(timezone.utc),
                'PROVISIONING_DISABLED': get_PROVISIONING_DISABLED(),
                'pending_refresh':pending_refresh,
                'pending_destroy':pending_destroy,
                }
        
        LOG.info(f"{request.user.username} {request.method} {nodeGroupObj.id} name:{nodeGroupObj} is_public:{nodeGroupObj.is_public} version:{nodeGroupObj.version} min_node_cap:{nodeGroupObj.cfg_asg.min} max_node_cap:{nodeGroupObj.cfg_asg.max} allow_deploy_by_token:{nodeGroupObj.allow_deploy_by_token} destroy_when_no_nodes:{nodeGroupObj.destroy_when_no_nodes} pending_refresh:{pending_refresh} pending_destroy:{pending_destroy}")
        #LOG.info("rendering users/node_group_manage.html")
        return render(request, 'users/node_group_manage.html', context)
    else:
        LOG.info(f"{request.user.username} {request.method} {nodeGroupObj} UNAUTHORIZED")
        messages.error(request,"Unauthorized access")
        return HttpResponse('Unauthorized', status=401)

@login_required(login_url='account_login')
@verified_email_required
def nodeGroupRefresh(request, pk):
    nodeGroupObj = NodeGroup.objects.get(id=pk)
    orgAccountObj = nodeGroupObj.org
    LOG.info("%s %s",request.method,orgAccountObj.name)
    if request.user.groups.filter(name='PS_Developer').exists() or orgAccountObj.owner == request.user:
        if request.method == 'POST':
            status = 200
            task_id = ''
            emsg = ''
            msg = ''
            try:
                try:
                    owner_ps_cmd = OwnerPSCmd.objects.get(cluster=nodeGroupObj, ps_cmd='Refresh')
                    msg = f" -- IGNORING -- Refresh {orgAccountObj.name} already queued for processing"
                except OwnerPSCmd.DoesNotExist:
                    owner_ps_cmd = OwnerPSCmd.objects.create(user=request.user, org=orgAccountObj, ps_cmd='Refresh', create_time=datetime.now(timezone.utc))
                    owner_ps_cmd.save()
                    msg = f"Refresh {orgAccountObj} queued for processing"
                messages.info(request, msg)             
                LOG.info(msg)
            except Exception as e:
                status = 500
                LOG.exception("caught exception:")
                emsg = "Caught exception:"+repr(e)
        # GET just displays node_group_manage
        LOG.info("redirect to node-group-mange")
        for handler in LOG.handlers:
            handler.flush()
        return redirect('node-group-mange',pk=orgAccountObj.id)
    else:
        messages.error(request,"Unauthorized access")
        return HttpResponse('Unauthorized', status=401)

@login_required(login_url='account_login')
@verified_email_required
def nodeGroupDestroy(request, pk):
    nodeGroupObj = NodeGroup.objects.get(id=pk)
    orgAccountObj = nodeGroupObj.org
    LOG.info("%s %s",request.method,orgAccountObj.name)
    if request.user.groups.filter(name='PS_Developer').exists() or orgAccountObj.owner == request.user:
        if request.method == 'POST':
            status = 200
            task_id = ''
            emsg = ''
            msg=''
            try:
                try:
                    owner_ps_cmd = OwnerPSCmd.objects.get(cluster=nodeGroupObj, ps_cmd='Destroy')
                    msg = f" -- IGNORING -- Destroy {orgAccountObj.name} already queued for processing"
                except OwnerPSCmd.DoesNotExist:
                    jrsp = remove_num_node_requests(request.user,orgAccountObj)
                    if jrsp['status'] == 'SUCCESS':
                        messages.info(request,jrsp['msg'])
                    else:
                        messages.error(request,jrsp['error_msg'])           
                    if nodeGroupObj.cnnro_ids is not None:
                        active_cnns = ClusterNumNode.objects.filter(id__in=nodeGroupObj.cnnro_ids)
                        if active_cnns.exists():
                            try:
                                for active_cnn in active_cnns:
                                    active_cnn.delete()
                                messages.info(request,"Successfully deleted active Node Group Num Node requests")
                            except Exception as e:
                                LOG.exception("caught exception:")
                                messages.error(request, 'Error deleting active Node Group Num Node requests')
                    owner_ps_cmd = OwnerPSCmd.objects.create(user=request.user, org=orgAccountObj, ps_cmd='Destroy', create_time=datetime.now(timezone.utc))
                    owner_ps_cmd.save()
                    msg = f"Destroy {orgAccountObj.name} queued for processing"
                messages.info(request, msg)             
                LOG.info(msg)
            except Exception as e:
                status = 500
                LOG.exception("caught exception:")
                messages.error(request, 'Error destroying cluster')
        # GET just displays node_group_manage
        LOG.info("redirect to node-group-mange")
        for handler in LOG.handlers:
            handler.flush()
        return redirect('node-group-mange',pk=orgAccountObj.id)
    else:
        messages.error(request,"Unauthorized access")
        return HttpResponse('Unauthorized', status=401)

@login_required(login_url='account_login')
@verified_email_required
def clearNumNodesReqs(request, pk):
    nodeGroupObj = NodeGroup.objects.get(id=pk)
    LOG.info(f"{request.user.username} {request.method} {nodeGroupObj} <owner:{nodeGroupObj.org.owner.username}>")
    LOG.info(f"request.POST:{request.POST} in group:{request.user.groups.filter(name='PS_Developer').exists()} is_owner:{orgAccountObj.owner == request.user}")
    if request.user.groups.filter(name='PS_Developer').exists() or nodeGroupObj.org.owner == request.user:
        if request.method == 'POST':
            jrsp = remove_num_node_requests(request.user,nodeGroupObj)
            if jrsp['status'] == 'SUCCESS':
                messages.info(request,jrsp['msg'])
            else:
                messages.error(request,jrsp['error_msg'])           
        # GET just displays node_group_manage
        LOG.info("redirect to node-group-mange")
        for handler in LOG.handlers:
            handler.flush()
        return redirect('node-group-mange',pk=nodeGroupObj.id)
    else:
        messages.error(request,"Unauthorized access")
        return HttpResponse('Unauthorized', status=401)

@login_required(login_url='account_login')
@verified_email_required
def clearActiveNumNodeReq(request, pk):
    nodeGroupObj = NodeGroup.objects.get(id=pk)
    LOG.info(f"{request.user.username} {request.method} {nodeGroupObj} <owner:{nodeGroupObj.org.owner.username}>")
    LOG.info(f"request.POST:{request.POST} in group:{request.user.groups.filter(name='PS_Developer').exists()} is_owner:{nodeGroupObj.org.owner == request.user}")
    if request.user.groups.filter(name='PS_Developer').exists() or nodeGroupObj.org.owner == request.user:
        if request.method == 'POST':
            active_cnns = ClusterNumNode.objects.filter(id__in=nodeGroupObj.cnnro_ids)
            if active_cnns.exists():
                for active_cnn in active_cnns:
                    active_cnn.delete()
                messages.info(request,"Successfully deleted active Node Group Num Node requests")
            else:
                messages.info(request,"No active Node Group Num Node request to delete")
        return redirect('node-group-mange',pk=nodeGroupObj.id)
    else:
        messages.error(request,"Unauthorized access")
        return HttpResponse('Unauthorized', status=401)

@login_required(login_url='account_login')
@verified_email_required
def nodeGroupConfigure(request, pk):
    nodeGroupObj = NodeGroup.objects.get(id=pk)
    orgAccountObj = nodeGroupObj.org
    LOG.info(f"{request.method} {nodeGroupObj}")
    updated = False
    if request.user.groups.filter(name='PS_Developer').exists() or orgAccountObj.owner == request.user:
        if request.method == 'POST':
            try:
                # USING an Unbound form and setting the object explicitly one field at a time!
                config_form = NodeGroupCfgForm(request.POST, instance=nodeGroupObj, available_versions=get_versions())
                emsg = ''
                if(config_form.is_valid()):
                    for field, value in config_form.cleaned_data.items():
                        LOG.info(f"Field: {field}, Value: {value}")
                    LOG.info(f"{nodeGroupObj} is_public:{nodeGroupObj.is_public} version:{nodeGroupObj.version} min_node_cap:{nodeGroupObj.cfg_asg.min} max_node_cap:{nodeGroupObj.cfg_asg.max} allow_deploy_by_token:{nodeGroupObj.allow_deploy_by_token} destroy_when_no_nodes:{nodeGroupObj.destroy_when_no_nodes}")
                    config_form.save()
                    updated = True
                    messages.success(request,f'{nodeGroupObj} cfg updated successfully')
                else:
                    emsg = f"Input Errors:{config_form.errors.as_text}"
                    messages.warning(request, emsg)
                    LOG.info(f"Did not save cluster_config for {nodeGroupObj} {emsg}")
                    LOG.info("These are the fields as submitted:")
                    for field, value in config_form.data.items():
                        LOG.info(f"Field: {field} - Value: {value}")            
            except Exception as e:
                LOG.exception("caught exception:")
                emsg = "Server ERROR"
                messages.error(request, emsg)
        try:
            if updated:
                # Force the cluster env to be reinitialized
                nodeGroupObj.provision_env_ready = False
                nodeGroupObj.save()
                LOG.info(f"saved nodeGroupObj for nodeGroupObj:{nodeGroupObj.id} name:{nodeGroupObj.name} is_public:{nodeGroupObj.is_public} version:{nodeGroupObj.version} ")
        except Exception as e:
            LOG.exception("caught exception:")
            emsg = "Server ERROR"
            messages.error(request, emsg)

        LOG.info(f"{request.user.username} {request.method} name:{nodeGroupObj} is_public:{nodeGroupObj.is_public} version:{nodeGroupObj.version} min_node_cap:{nodeGroupObj.cfg_asg.min} max_node_cap:{nodeGroupObj.cfg_asg.max} allow_deploy_by_token:{nodeGroupObj.allow_deploy_by_token} destroy_when_no_nodes:{nodeGroupObj.destroy_when_no_nodes}")
        LOG.info("redirect to node-group-mange")
        for handler in LOG.handlers:
            handler.flush()
        return redirect('node-group-mange',pk=nodeGroupObj.id)
    else:
        messages.error(request,"Unauthorized access")
        return HttpResponse('Unauthorized', status=401)

@login_required(login_url='account_login')
@verified_email_required
def orgAccountHistory(request, pk):
    orgAccountObj = OrgAccount(id=pk)
    LOG.info("%s %s",request.method,orgAccountObj.name)
    if request.user.groups.filter(name='PS_Developer').exists() or orgAccountObj.owner == request.user:
        cost_accounting_org(orgAccountObj)
        context = {'org': orgAccountObj,'today': datetime.now()} # TBD do we need tz=timezone.utc ?
        return render(request, 'users/org_account_history.html', context)
    else:
        messages.error(request,"Unauthorized access")
        return HttpResponse('Unauthorized', status=401)


@login_required(login_url='account_login')
@verified_email_required
def nodeGroupAccountHistory(request, pk):
    nodeGroupObj = NodeGroup.objects.get(id=pk)
    orgAccountObj = nodeGroupObj.org
    LOG.info(f"{request.method} {nodeGroupObj}")
    if request.user.groups.filter(name='PS_Developer').exists() or orgAccountObj.owner == request.user:
        cost_accounting_cluster(nodeGroupObj)
        context = {'org': orgAccountObj,'today': datetime.now()} # TBD do we need tz=timezone.utc ?
        return render(request, 'users/node_group_account_history.html', context)
    else:
        messages.error(request,"Unauthorized access")
        return HttpResponse('Unauthorized', status=401)


@login_required(login_url='account_login')
@verified_email_required
def ajaxOrgAccountHistory(request):
    if request.user.groups.filter(name='PS_Developer').exists() or orgAccountObj.owner == request.user:
        if(request.headers.get('x-requested-with') == 'XMLHttpRequest') and (request.method == 'GET'):
            orgAccountObj = OrgAccount.objects.get(id=request.GET.get("org_uuid", None))
            gran = request.GET.get("granularity", "undefined")
            LOG.info("%s %s %s", orgAccountObj.name,request.method, request.GET.get("granularity", "undefined"))
            got_data, orgCostObj = get_db_cluster_cost(gran, orgAccountObj)
            LOG.info("%s crt:%s", orgAccountObj.name, orgCostObj.cost_refresh_time)
            if got_data:
                status = 200
                context = {'ccr': orgCostObj.ccr,
                        'crt':  datetime.strftime(orgCostObj.cost_refresh_time, FMT_TZ)}
            else:
                status = 500
                context = {'ccr': {}}
            return JsonResponse(context, status=status)
        else:
            LOG.warning("%s %s redirected! browse",request.method,orgAccountObj.name)
            return redirect('browse')
    else:
        messages.error(request,"Unauthorized access")
        return HttpResponse('Unauthorized', status=401)

@login_required(login_url='account_login')
@verified_email_required
def ajaxNodeGroupAccountHistory(request):
    nodeGroupObj = NodeGroup.objects.get(pk=request.GET.get("cluster_uuid", None))
    orgAccountObj = nodeGroupObj.org
    if request.user.groups.filter(name='PS_Developer').exists() or orgAccountObj.owner == request.user:
        if(request.headers.get('x-requested-with') == 'XMLHttpRequest') and (request.method == 'GET'):
            gran = request.GET.get("granularity", "undefined")
            LOG.info(f' {nodeGroupObj} {request.method} {request.GET.get("granularity", "undefined")}')
            got_data, orgCostObj = get_db_cluster_cost(gran, nodeGroupObj)
            LOG.info(f"{nodeGroupObj} crt:{orgCostObj.cost_refresh_time}")
            if got_data:
                status = 200
                context = {'ccr': orgCostObj.ccr,
                        'crt':  datetime.strftime(orgCostObj.cost_refresh_time, FMT_TZ)}
            else:
                status = 500
                context = {'ccr': {}}
            return JsonResponse(context, status=status)
        else:
            LOG.warning(f"{request.method} {nodeGroupObj} redirected to browse ")
            return redirect('browse')
    else:
        messages.error(request,"Unauthorized access")
        return HttpResponse('Unauthorized', status=401)


@login_required(login_url='account_login')
@verified_email_required
def orgAccountForecast(request, pk):
    orgAccountObj = OrgAccount.objects.get(pk)
    nodeGroupObj = NodeGroup.objects.get(org__name=orgAccountObj.name)
    LOG.info("%s %s", request.method, orgAccountObj.name)
    if request.user.groups.filter(name='PS_Developer').exists() or orgAccountObj.owner == request.user:
        cost_accounting_org(orgAccountObj)
        context = {'org': orgAccountObj, 'cluster':nodeGroupObj}
        LOG.info('rendering org_account_forecast')
        return render(request, 'users/org_account_forecast.html', context)
    else:
        messages.error(request,"Unauthorized access")
        return HttpResponse('Unauthorized', status=401)

@login_required(login_url='account_login')
@verified_email_required
def nodeGroupAccountForecast(request, pk):
    nodeGroupObj = NodeGroup.objects.get(id=pk)
    orgAccountObj = nodeGroupObj.org
    LOG.info(f"{request.method} {nodeGroupObj}")
    if request.user.groups.filter(name='PS_Developer').exists() or orgAccountObj.owner == request.user:
        cost_accounting_cluster(nodeGroupObj)
        show_min_shutdown_date = (nodeGroupObj.min_ddt <= (datetime.now(timezone.utc) + timedelta(days=DISPLAY_EXP_TM)))
        show_cur_shutdown_date = (nodeGroupObj.cur_ddt <= (datetime.now(timezone.utc) + timedelta(days=DISPLAY_EXP_TM)))
        context = {'org': orgAccountObj, 'cluster':nodeGroupObj, 'show_cur_shutdown_date': show_cur_shutdown_date, 'show_min_shutdown_date': show_min_shutdown_date}
        LOG.info('rendering org_account_forecast')
        return render(request, 'users/org_account_forecast.html', context)
    else:
        messages.error(request,"Unauthorized access")
        return HttpResponse('Unauthorized', status=401)

@login_required(login_url='account_login')
@verified_email_required
def ajaxOrgAccountForecast(request):
    if(request.headers.get('x-requested-with') == 'XMLHttpRequest') and (request.method == 'GET'):
        orgAccountObj = OrgAccount.objects.get(id=request.GET.get("org_uuid", None))
        LOG.info(f"{request.method} {orgAccountObj.name} {request.GET.get('granularity', 'undefined')}")
        gran = request.GET.get("granularity", "undefined")
        if gran == 'HOURLY':
            fc_min = orgAccountObj.fc_min_hourly
            fc_cur = orgAccountObj.fc_cur_hourly
            fc_max = orgAccountObj.fc_max_hourly
            br_min = orgAccountObj.min_hrly
            br_cur = orgAccountObj.cur_hrly
            br_max = orgAccountObj.max_hrly
            got_data, orgCostObj  = get_db_cluster_cost("HOURLY", orgAccountObj)
        elif gran == 'DAILY':
            fc_min = orgAccountObj.fc_min_daily
            fc_cur = orgAccountObj.fc_cur_daily
            fc_max = orgAccountObj.fc_max_daily
            br_min = orgAccountObj.min_hrly*24
            br_cur = orgAccountObj.cur_hrly*24
            br_max = orgAccountObj.max_hrly*24
            got_data, orgCostObj  = get_db_cluster_cost("DAILY", orgAccountObj)
        elif gran == 'MONTHLY':
            fc_min = orgAccountObj.fc_min_monthly
            fc_cur = orgAccountObj.fc_cur_monthly
            fc_max = orgAccountObj.fc_max_monthly
            br_min = orgAccountObj.min_hrly*24*30
            br_cur = orgAccountObj.cur_hrly*24*30
            br_max = orgAccountObj.max_hrly*24*30
            got_data, orgCostObj  = get_db_cluster_cost("MONTHLY", orgAccountObj)
        if got_data:
            cost_refresh_time = orgCostObj.cost_refresh_time
            cost_refresh_time_str = datetime.strftime(cost_refresh_time,"%Y-%m-%d %H:%M:%S %Z")
        #LOG.info("%s %s %s %s",gran,got_data,cost_refresh_time,cost_refresh_time_str)
        #LOG.info("gran:%s br_min:%2g br_cur:%2g br_max:%2g", gran, br_min, br_cur, br_max)
        context = {'br_min': br_min, 'br_cur': br_cur, 'br_max': br_max, 'gran': gran, 'fc_min': fc_min,
                   'fc_cur': fc_cur, 'fc_max': fc_max, 'cost_refresh_time': cost_refresh_time, 'cost_refresh_time_str': cost_refresh_time_str}
        status = 200
        # else:
        #     status = 500
        #     context = {'cost': {}}
        return JsonResponse(context, status=status)
    else:
        LOG.warning(f"{request.method} {orgAccountObj.name} redirected to browse")
        return redirect('browse')

@login_required(login_url='account_login')
@verified_email_required
def ajaxNodeGroupAccountForecast(request):
    if(request.headers.get('x-requested-with') == 'XMLHttpRequest') and (request.method == 'GET'):
        nodeGroupObj = NodeGroup.objects.get(id=request.GET.get("cluster_uuid", None))
        LOG.info(f"{request.method} {nodeGroupObj} {request.GET.get('granularity', 'undefined')}")
        gran = request.GET.get("granularity", "undefined")
        if gran == 'HOURLY':
            fc_min = nodeGroupObj.fc_min_hourly
            fc_cur = nodeGroupObj.fc_cur_hourly
            fc_max = nodeGroupObj.fc_max_hourly
            br_min = nodeGroupObj.min_hrly
            br_cur = nodeGroupObj.cur_hrly
            br_max = nodeGroupObj.max_hrly
            got_data, clusterCostObj  = get_db_cluster_cost("HOURLY", nodeGroupObj)
        elif gran == 'DAILY':
            fc_min = nodeGroupObj.fc_min_daily
            fc_cur = nodeGroupObj.fc_cur_daily
            fc_max = nodeGroupObj.fc_max_daily
            br_min = nodeGroupObj.min_hrly*24
            br_cur = nodeGroupObj.cur_hrly*24
            br_max = nodeGroupObj.max_hrly*24
            got_data, clusterCostObj  = get_db_cluster_cost("DAILY", nodeGroupObj)
        elif gran == 'MONTHLY':
            fc_min = nodeGroupObj.fc_min_monthly
            fc_cur = nodeGroupObj.fc_cur_monthly
            fc_max = nodeGroupObj.fc_max_monthly
            br_min = nodeGroupObj.min_hrly*24*30
            br_cur = nodeGroupObj.cur_hrly*24*30
            br_max = nodeGroupObj.max_hrly*24*30
            got_data, clusterCostObj  = get_db_cluster_cost("MONTHLY", nodeGroupObj)
        if got_data:
            cost_refresh_time = clusterCostObj.cost_refresh_time
            cost_refresh_time_str = datetime.strftime(cost_refresh_time,"%Y-%m-%d %H:%M:%S %Z")
        #LOG.info("%s %s %s %s",gran,got_data,cost_refresh_time,cost_refresh_time_str)
        #LOG.info("gran:%s br_min:%2g br_cur:%2g br_max:%2g", gran, br_min, br_cur, br_max)
        context = {'br_min': br_min, 'br_cur': br_cur, 'br_max': br_max, 'gran': gran, 'fc_min': fc_min,
                   'fc_cur': fc_cur, 'fc_max': fc_max, 'cost_refresh_time': cost_refresh_time, 'cost_refresh_time_str': cost_refresh_time_str}
        status = 200
        # else:
        #     status = 500
        #     context = {'cost': {}}
        return JsonResponse(context, status=status)
    else:
        LOG.warning(f"{request.method} {nodeGroupObj} redirected to browse")
        return redirect('browse')

def set_intermediate_fields(orgBudgetForm,ng_form):
    orgBudgetForm.add_to_field(field_name='max_allowance' , amount=ng_form.cleaned_data['budget_max_allowance'])
    orgBudgetForm.add_to_field(field_name='monthly_allowance' , amount=ng_form.cleaned_data['budget_monthly_allowance'])
    orgBudgetForm.add_to_field(field_name='balance' , amount=ng_form.cleaned_data['budget_balance'])


@login_required(login_url='account_login')
@verified_email_required
@transaction.atomic
def orgBudget(request, pk):
    try:
        orgAccountObj = OrgAccount.objects.get(id=pk)
        LOG.info(f"{request.method} {orgAccountObj.name}")

        # Check user permissions
        if not (request.user.groups.filter(name='PS_Developer').exists() or orgAccountObj.owner == request.user):
            messages.warning(request, 'Insufficient privileges')
            LOG.warning("%s %s redirected! browse", request.method, orgAccountObj.name)
            return redirect('browse')

        content_type = ContentType.objects.get_for_model(orgAccountObj)

        # Check if a related Budget already exists for the OrgAccount instance
        has_budget = Budget.objects.filter(content_type=content_type, object_id=orgAccountObj.id).exists()
        # This is needed for backward compatibility with existing OrgAccounts
        # If a related Budget doesn't exist, create one and save it
        if not has_budget:
            new_budget = Budget.objects.create(max_allowance=0.0, monthly_allowance=0.0, balance=0.0,content_object=orgAccountObj)
            new_budget.save()                

        orgBudgetObj = orgAccountObj.budget.first()
        if request.method == "POST":
            orgBudgetForm = OrgBudgetForm(instance=orgBudgetObj,org=orgAccountObj)
            nodeGroupCreateFormSet = NodeGroupCreateFormSet(request.POST, queryset=NodeGroup.objects.filter(org=orgAccountObj),org=orgAccountObj)
            all_node_groups_valid = all([ng_create_formset.is_valid() for ng_create_formset in nodeGroupCreateFormSet])
            LOG.info(f"POST all_node_groups_valid:{all_node_groups_valid}")
            if all_node_groups_valid:
                cnt=0
                try:
                    for ng_form in nodeGroupCreateFormSet:
                        ng_form.save()
                        cnt +=1
                        set_intermediate_fields(orgBudgetForm=orgBudgetForm,ng_form=ng_form)
                    orgBudgetForm.save()
                    messages.success(request, f'Org Account {orgAccountObj.name} successfully saved with {cnt} Node Groups')
                    LOG.info( f'Org Account {orgAccountObj.name} successfully saved org bugdet and {cnt} node group budgets')
                except Exception as e:
                    LOG.exception("caught exception:")
                    messages.error(request, f'Error saving Org Account {orgAccountObj.name} with {cnt} Node Groups')
            else:
                form_errors = orgBudgetForm.errors.as_text() + " " + " ".join([ng_create_formset.errors.as_text() for ng_create_formset in nodeGroupCreateFormSet])
                emsg = f'Errors in form: {form_errors}'
                LOG.error(f"POST {emsg}")
                messages.error(request, f'{emsg}')
            nodeGroupCreateFormSet = NodeGroupCreateFormSet(request.POST, queryset=NodeGroup.objects.filter(org=orgAccountObj),org=orgAccountObj)
        else:
            nodeGroupCreateFormSet = NodeGroupCreateFormSet(queryset=NodeGroup.objects.filter(org=orgAccountObj),org=orgAccountObj)
            # Initialize forms and formsets to reflect the summed values
            orgBudgetForm = OrgBudgetForm(instance=orgBudgetObj)
            all_node_groups_valid = all([ng_create_formset.is_valid() for ng_create_formset in nodeGroupCreateFormSet])
            LOG.info(f"GET all_node_groups_valid:{all_node_groups_valid}")
            if all_node_groups_valid:
                cnt=0
                try:
                    for ng_form in nodeGroupCreateFormSet:
                        set_intermediate_fields(orgBudgetForm=orgBudgetForm,ng_form=ng_form)
                        cnt +=1
                except Exception as e:
                    LOG.exception("caught exception:")
                    messages.error(request, f'Error initializing Org Account {orgAccountObj.name} with {cnt} Node Groups')
        context = {'orgAccountObj': orgAccountObj, 'orgBudgetForm':orgBudgetForm, 'nodeGroupCreateFormSet': nodeGroupCreateFormSet}    
        return render(request, 'users/org_budget.html', context)
    except OrgAccount.DoesNotExist:
        messages.error(request, "Organization does not exist!")
        return redirect('browse')
    except Exception as e:
        LOG.exception("Caught unexpected exception")
        messages.error(request, f"Unexpected error: {repr(e)}")
        return redirect('browse')

@login_required(login_url='account_login')
@verified_email_required
@transaction.atomic
# atomic ensures org and cluster and orgCost are always created together
def orgAccountCreate(request):
    try:
        # User must be in the PS_Developer group or the owner to modify the profile
        if request.user.groups.filter(name='PS_Developer').exists():
            if request.method == 'POST':
                org_account_form = OrgCreateForm(request.POST)
                new_org,msg,emsg = add_obj_cost(org_account_form)
                if msg != '':
                    messages.info(request,msg)
                if emsg != '':
                    messages.error(request,emsg)
                return redirect('browse')
            else:
                org_account_form = OrgCreateForm()
                return render(request, 'users/org_create.html', {'org_form': org_account_form})
        else:
            messages.warning(request, 'Insufficient privileges')
            return redirect('browse')

    except Exception as e:
        LOG.exception("caught exception:")
        emsg = "Caught exception:"+repr(e)
        messages.error(request, emsg)
        return redirect('browse')

@login_required(login_url='account_login')
@verified_email_required
@transaction.atomic
# atomic ensures org and cluster and orgCost are always created together
def orgAccountDelete(request,pk):
    try:
        # User must be in the PS_Developer group 
        if request.user.groups.filter(name='PS_Developer').exists():
            if request.method == 'POST':
                orgAccountObj = OrgAccount.objects.get(id=pk)
                name = orgAccountObj.name
                orgAccountObj.delete()
                messages.success(request,f"Organization {name} deleted")
            else:
                LOG.error(f"Invalid access attempted by {request.user.username}")
        else:
            messages.warning(request, 'Insufficient privileges')
    except Exception as e:
        LOG.exception("caught exception:")
        emsg = "Caught exception:"+repr(e)
        messages.error(request, emsg)
    return redirect('browse')

@login_required(login_url='account_login')
@verified_email_required
@transaction.atomic
# atomic ensures org and cluster and Cost are always created together
def nodeGroupCreate(request,pk=None):
    try:
        orgId = pk        
        # Fetch the Org object using orgId
        orgAccountObj = OrgAccount.objects.get(id=orgId)  # Replace "Org" with your actual Org model's name
        if not request.user.groups.filter(name='PS_Developer').exists():
            messages.warning(request, 'Insufficient privileges')
            if request.is_ajax():
                return JsonResponse({'success': False, 'error': 'Insufficient privileges'})
            return redirect('org-config')
        
        if request.method == 'POST':
            form = NodeGroupCreateForm(request.POST)
            form.instance.org = orgAccountObj
            if form.is_valid():
                new_node_group, msg, emsg = add_node_group_cost(form, True)
            
            if request.is_ajax():
                if msg:
                    return JsonResponse({'success': True, 'message': msg})
                elif emsg:
                    return JsonResponse({'success': False, 'error': emsg})
            
            if msg:
                messages.info(request, msg)
            if emsg:
                messages.error(request, emsg)
            return redirect('org-config')
        else:
            form = NodeGroupCreateForm()
            return render(request, 'users/node_group_create.html', {'form': form})
    
    except Exception as e:
        LOG.exception("caught exception:")
        emsg = "Caught exception: " + repr(e)
        messages.error(request, emsg)
        if request.is_ajax():
            return JsonResponse({'success': False, 'error': emsg})
        return redirect('browse')

@login_required(login_url='account_login')
def userProfile(request):
    LOG.info(request.method)
    try:
        userObj = request.user
        LOG.info("%s %s",request.method,userObj.username)
        if request.method == "POST":
            f = UserProfileForm(request.POST, instance=userObj)
            LOG.info("Form")
            if(f.is_valid()):
                LOG.info("Form save")
                f.save()
                messages.info(request, "Profile successfully updated")
                return redirect('browse')
            else:
                LOG.error("Form error:%s", f.errors.as_text)
                messages.warning(request, 'error in form')
        f = UserProfileForm(instance=userObj)
        context = {'user': userObj, 'form': f}
        return render(request, 'users/user_profile.html', context)

    except Exception as e:
        LOG.exception("caught exception:")
        emsg = "Caught exception:"+repr(e)
        LOG.error(emsg)
        messages.error(request, 'Server Error')
        LOG.warning("%s redirected! browse",request.user)
        return redirect('browse')

@login_required(login_url='account_login')
def browse(request):
    LOG.info(request.method)
    try:
        active_user = request.user
        if(active_user.is_superuser):
            LOG.error("Invalid access attempted by superuser")
            messages.error(request, 'Superusers should not access regular site')
            return redirect('/admin')
        else:
            org_member = {}
            is_member_of_org = {}
            org_has_pending_m = {}
            user_is_owner_of_org = {}
            org_by_name = {}
            clusters_by_org_name = {}
            org_has_public_cluster = {}
            org_show_shutdown_date = {}
            unaffiliated_with_priv_clusters = []
            unaffiliated_with_public_clusters = []
            with_membership_not_owner = []
            any_ownerships = False
            user_is_developer = active_user.groups.filter(name='PS_Developer').exists()
            
            org_accounts = OrgAccount.objects.all()
            user_memberships = Membership.objects.filter(user=active_user)

            for orgAccountObj in org_accounts:
                try:
                    o = orgAccountObj
                    if o.name == 'uninitialized':
                        LOG.error(f"IGNORING org:{o.name}")
                        LOG.error(f"DELETING org:{o.name}")
                        o.delete()
                    else:    
                        found_m = False
                        pend = False
                        #cluster_show_shutdown_date.update({o.name: (nodeGroupObj.budget.min_ddt <= (datetime.now(timezone.utc) + timedelta(days=365)))})
                        if o.owner == active_user:
                            any_ownerships = True
                        user_is_owner_of_org.update({o.name: (o.owner == active_user)})
                        org_by_name.update({o.name: o})
                        clusters_in_org = NodeGroup.objects.filter(org__name=o.name)
                        clusters_by_org_name.update({o.name: clusters_in_org})
                        for nodeGroupObj in clusters_in_org:
                            if nodeGroupObj.is_public:
                                org_has_public_cluster.update({o.name: True})
                        #LOG.info(f"number of memberships:{user_memberships.count()}")
                        for m in user_memberships:
                            #LOG.info(f"membership:{m}")
                            if o is not None and m.org is not None:
                                if o.name == m.org.name:
                                    found_m = True
                                    #LOG.info(f"found membership:{m} for org:{o.name} user:{active_user.username}")
                                    if not (o.owner == active_user):
                                        with_membership_not_owner.append(o)
                                    org_member.update({o.name: m})
                                    pend = not m.active
                        if not found_m:
                            has_priv_cluster = False
                            has_public_cluster = False
                            for nodeGroupObj in clusters_in_org:
                                if nodeGroupObj.is_public:
                                    has_public_cluster = True
                                else:
                                    has_priv_cluster = True
                            if has_priv_cluster:
                                unaffiliated_with_priv_clusters.append(o)
                            if has_public_cluster:
                                unaffiliated_with_public_clusters.append(o)
                        is_member_of_org.update({o.name: found_m})
                        org_has_pending_m.update({o.name: pend})
                except Exception as e:
                    LOG.exception("caught exception:")

        context = { 'org_member': org_member,
                    'is_member_of_org': is_member_of_org,
                    'org_by_name': org_by_name,
                    'org_has_pending_m': org_has_pending_m,
                    'org_accounts': org_accounts,
                    'org_show_shutdown_date': org_show_shutdown_date,
                    'is_developer': active_user.groups.filter(name='PS_Developer').exists(),
                    'user_is_owner_of_org': user_is_owner_of_org,
                    'user_is_developer': user_is_developer,
                    'unaffiliated_with_priv_clusters': unaffiliated_with_priv_clusters,
                    'unaffiliated_with_public_clusters': unaffiliated_with_public_clusters,
                    'any_ownerships': any_ownerships,
                    'with_membership_not_owner': with_membership_not_owner,
                    'clusters_by_org_name': clusters_by_org_name,
                    'org_has_public_cluster': org_has_public_cluster,
                    'PROVISIONING_DISABLED': get_PROVISIONING_DISABLED(),
                    }

        # this filter 'get_item' is used inside the template
        @register.filter
        def get_item(dictionary, key):
            return dictionary.get(key)
        if(OrgAccount.objects.all().count() == 0):
            LOG.info("No Organizations exist!")
            messages.info(
                request, 'No organizations exist yet; Have admin user create them')

    except Exception as e:
        LOG.exception("caught exception:")
        #emsg = "SW Error:%"+repr(e)
        return HttpResponse(status=500)
    for handler in LOG.handlers:
        handler.flush()
    return render(request, 'users/browse.html', context)

@login_required(login_url='account_login')
@verified_email_required
def memberships(request):  # current session user
    #LOG.info(request.method)
    active_user = request.user
    org_cluster_deployed_state = {}
    org_cluster_connection_status = {}
    org_cluster_active_ps_cmd = {}
    user_is_owner_of_org = {}
    membershipObjs = Membership.objects.filter(user=active_user)
    displayed_memberships = []
    for m in membershipObjs:
        if m.user.username.strip() == active_user.username.strip():
            displayed_memberships.append(m)
            o = OrgAccount.get(name=m.org.name)
            nodeGroupObj = NodeGroup.objects.get(org__name=o.name)
            org_cluster_deployed_state.update({o.name: nodeGroupObj.deployed_state})
            org_cluster_active_ps_cmd.update({o.name: nodeGroupObj.active_ps_cmd})
            user_is_owner_of_org.update({o.name: (o.owner == request.user)})
    #LOG.info(org_cluster_deployed_state)

    context = { 'user': active_user,
                'memberships': displayed_memberships,
                'org_cluster_deployed_state': org_cluster_deployed_state,
                'org_cluster_connection_status': org_cluster_connection_status,
                'org_cluster_active_ps_cmd': org_cluster_active_ps_cmd,
                'is_developer': request.user.groups.filter(name='PS_Developer').exists(),
                'is_developer': request.user.groups.filter(name='PS_Developer').exists(),
                'user_is_owner_of_org': user_is_owner_of_org
                }

    # this filter 'get_item' is used inside the template
    @register.filter
    def get_item(dictionary, key):
        return dictionary.get(key)
    if(membershipObjs.count() > 0):
        return render(request, 'users/memberships.html', context)
    else:
        messages.info(request,
                      'You have no memberships; Find your organization then Click "Request Membership" ')
        return redirect('browse')

@login_required(login_url='account_login')
@verified_email_required
def cancelMembership(request, pk):
    LOG.info(request.method)
    membershipObj = Membership.objects.get(id=pk)
    if request.method == 'POST':
        membershipObj.delete()
        return redirect('browse')
    context = {'object': membershipObj}
    return render(request, 'users/confirm_cancel_membership.html', context)


@login_required(login_url='account_login')
@verified_email_required
def reqNewMembership(request, pk):
    # create an instance
    q = Membership.objects.filter(org=pk, user=ruser)
    attempt_succeeded = True
    first_time = False
    try:
        if(q.count() == 0):
            m = Membership()
            m.user = request.user
            m.org = OrgAccount.objects.get(id=pk)
            m.save()
            first_time = True
            messages.success(
                request, f'{m.user.first_name} {m.user.last_name} your membership to {m.org} was recieved; We will review your request and if accepted we will send you an email with an activation notice')
        else:
            m = q.get()
        if(q.count() != 1):
            LOG.info(
                f"A membership record already exists for {m.user.username} {m.org}")

    except (OrgAccount.DoesNotExist, TypeError):
        attempt_succeeded = False
        messages.error(request, 'Membership request failed; Org does not exist or has no owner?')

    # Tell user what to do
    type = "Membership"
    context = {'m': m, 'user': request.user,
               'account_type': type, 'first_time': first_time}
    if(attempt_succeeded):
        # TBD change to message
        return render(request, 'users/req_new_account_notice.html', context)
    else:
        return render(request, 'users/req_new_account_failed_notice.html', context)

@login_required(login_url='account_login')
@verified_email_required
def provSysAdmin(request):
    ndx = 0
    try:
        for site in Site.objects.all():
            LOG.info(f"site[{ndx}] id:{site.id} {type(site.id)} name:{site.name} domain:{site.domain}")
            ndx = ndx+1
    except Exception as e:
        LOG.exception("caught exception:")

    if request.user.groups.filter(name='PS_Developer').exists():
        return render(request, 'prov_sys_admin.html', {'PROVISIONING_DISABLED': get_PROVISIONING_DISABLED()})
    else:
        messages.error(request, 'You Do NOT have privileges to access this page')
        return redirect('browse')

@login_required(login_url='account_login')
@verified_email_required
def disableProvisioning(request):    
    if request.user.groups.filter(name='PS_Developer').exists():
        set_PROVISIONING_DISABLED('True')
        messages.warning(request, 'You have disabled provisioning!')
    else:
        LOG.warning(f"User {request.user.username} attempted to disable provisioning")
        messages.error(request, 'You are not a PS_Developer')
    return redirect('browse')