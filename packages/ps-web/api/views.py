from rest_framework.response import Response
from rest_framework import status, generics
from rest_framework.response import Response
from rest_framework import serializers
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view
from rest_framework_simplejwt.settings import api_settings

from rest_framework.decorators import api_view
from api.serializers import MembershipSerializer, OrgTokenObtainPairSerializer, OrgTokenRefreshSerializer
from rest_framework_simplejwt.backends import TokenBackend
from rest_framework.exceptions import ValidationError
from django.views.decorators.csrf import csrf_exempt
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from datetime import datetime, timezone, timedelta
from users.models import Membership, OrgAccount, User, NodeGroup, ClusterNumNode
from users.ps_errors import ClusterDeployAuthError
import logging
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import api_view
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import TokenBackendError
from users.tasks import process_num_nodes_api,update_cur_num_nodes,remove_num_node_requests,set_PROVISIONING_DISABLED
from users.global_constants import *
from oauth2_provider.views.generic import ProtectedResourceView
from django.http import JsonResponse
from django.contrib.auth import authenticate, login

LOG = logging.getLogger('django')
JWT_authenticator = JWTAuthentication()


class DummySerializer(serializers.Serializer):
    pass



def get_user_in_token(request):
    jrsp = {'status': '','error_msg': "", 'msg': ""}
    try:
        #LOG.info(f"{name} {request}")
        status = 200
        msg = ''
        user_in_token = None

        # authenitcate() verifies and decode the token
        # if token is invalid, it raises an exception and returns 401
        response = JWT_authenticator.authenticate(request)
        if response is not None:
            # unpacking
            user_in_token , token = response
            jrsp = {'status': 'Success','error_msg': "", 'msg': f'User {user_in_token.username} is authenticated'}
            #LOG.info(f"token claims: {token.payload}")
        else:
            msg = "No token is provided in the header or the header is missing"
            LOG.error(msg)
            jrsp = {'status': "FAILED","error_msg":msg}
            status = 400
    except KeyError as v:
        LOG.exception("caught KeyError exception:")
        jrsp = {'status': "FAILED","error_msg":"invalid token ke"}
        status=400
    except ValidationError as v:
        LOG.exception("caught ValidationError exception:")
        jrsp = {'status': "FAILED","error_msg":"invalid token ve"}
        status=400
    except Exception as e:
        LOG.exception("caught exception:")
        jrsp = {'status': "FAILED","error_msg":"Server Error"}
        status = 500
    LOG.info(f" returns status:{status} jrsp:{jrsp} user_in_token:{user_in_token.username if user_in_token is not None else None}")
    return jrsp,status,user_in_token

def get_token_org_active_membership(request,org_name):
    try:
        #LOG.info(f"{org_name} {request}")
        status = 200
        msg = ''
        active = False
        user_in_token = None
        now = datetime.now(timezone.utc)
        token_expire_date = now

        jrsp,status,user_in_token = get_user_in_token(request)
        if status != 200:
            LOG.warning(f"get_user_in_token returned {status}")
            return jrsp,status,active,user_in_token,token_expire_date

        try:
            orgAccountObj = OrgAccount.objects.get(name=org_name)
        except (OrgAccount.DoesNotExist):
            msg = f"Unknown org:{org_name}"
            LOG.warning(msg)
            jrsp = {'status': "FAILED","error_msg":msg}
            return jrsp,400,False,'',token_expire_date
        try:
            token = request.META.get('HTTP_AUTHORIZATION', " ").split(' ')[1]
            # Always verify=False for HMAC algorithm
            valid_data = TokenBackend(algorithm='HS256').decode(token, verify=False)
        except TokenBackendError as e:
            LOG.exception(e)
            jrsp = {'status': "FAILED","error_msg":"INVALID token"}
            return jrsp,400,False,'',token_expire_date  
        except Exception as e:
            LOG.exception(e)
            jrsp = {'status': "FAILED","error_msg":"INVALID token"}
            return jrsp,400,False,'',token_expire_date  

        token_expires = float(valid_data['exp'])
        jti = valid_data['jti']
        token_expire_date = datetime.fromtimestamp(token_expires,tz=timezone.utc).replace(microsecond=0)
        LOG.info(f"exp:{token_expire_date} <= now:{now}?")
        if(token_expire_date <= now): # Framework should catch this ... just in case
            msg = "Token is expired"
            LOG.warning(msg)
            jrsp = {'status': "FAILED","error_msg":"Token is expired"}
            status=400
        else:
            LOG.info(f"token_expire_time: {datetime.strftime(datetime.fromtimestamp(token_expires,tz=timezone.utc), FMT)}")
            org_name_in_token = valid_data['org_name']
            user_id_in_token = valid_data['user_id']
            try:
                user_in_token = User.objects.get(id=user_id_in_token)
            except (User.DoesNotExist):
                LOG.exception("caught exception:")
                jrsp = {'status': "FAILED","error_msg":"user id in token is invalid"}
                status=400
                return jrsp,status,False,'',token_expire_date                 
            try:
                membership  = Membership.objects.filter(org=orgAccountObj).get(user=user_in_token)               
            except (Membership.DoesNotExist):
                LOG.exception("caught exception:")
                jrsp = {'status': "FAILED","error_msg":f"user{user_in_token.username} is not a member of {orgAccountObj.name}?"}
                status=400
                return jrsp,status,False,'',token_expire_date   
            try:
                if org_name == org_name_in_token:
                    serializer  = MembershipSerializer(membership, many=False)
                    #LOG.info('serializer_data:%s',serializer.data)
                    active = serializer.data['active']
                    LOG.info(active)
                    jrsp = {'active': active}
                    status = 200
                else:
                    jrsp = {'active': "false"}
                    emsg = f"Token claim org:{org_name_in_token} does not match organization given:{org_name} "
                    LOG.warning(emsg)
                    jrsp = {'status': "FAILED","error_msg":emsg}
                    status=400
            except Exception as e:
                LOG.exception("caught exception:")
                jrsp = {'status': "FAILED","error_msg":"Server Error"}
                status = 500
    except KeyError as v:
        LOG.exception("caught KeyError exception:")
        jrsp = {'status': "FAILED","error_msg":"invalid token ke"}
        status=400
    except ValidationError as v:
        LOG.exception("caught ValidationError exception:")
        jrsp = {'status': "FAILED","error_msg":"invalid token ve"}
        status=400
    except Exception as e:
        LOG.exception("caught exception:")
        jrsp = {'status': "FAILED","error_msg":"Server Error"}
        status = 500
    LOG.info(f" returns status:{status} active:{active} jrsp:{jrsp} username:{user_in_token.username}")
    return jrsp,status,active,user_in_token,token_expire_date 

class MembershipStatusView(generics.RetrieveAPIView):
    '''
        Takes an org_name and returns the membership status ("Active": True/False ) of the user in organization contained in the claims of the token.
        Users membership is controlled by the organization's admins.
        NOTE: the org_name passed as a parameter must match the organization of the claim in the token that is used for authorization.
    '''
    serializer_class = DummySerializer
    def get(self, request, org_name, *args, **kwargs):
        jrsp, http_status, active, user, token_expire_date = get_token_org_active_membership(request, org_name)
        return Response(jrsp, status=http_status)   

class DesiredNumNodesView(generics.UpdateAPIView):
    '''
        Takes an name and desired_num_nodes and creates an ClusterNumNode request for the org. 
        This will go into the pool of requests for the org.
        This request will expire when the token expires (one hour). 
        The msg field of the JSON response will contain the this expiration date/time.
        The current highest number of nodes in the pool of all the requests that are not expired will be deployed to the org. 
        Expired entries are immediately removed from the pool and the number of nodes are adjusted if needed.
    '''
    serializer_class = DummySerializer
    def update(self, request, name, cluster_name, desired_num_nodes, *args, **kwargs):
        try:
            jrsp, http_status, active, user, token_expire_date = get_token_org_active_membership(request, org_name)
            if http_status == status.HTTP_200_OK:
                if active:
                    LOG.info(f"type(token_expire_date):{type(token_expire_date)}")
                    LOG.info(f"token_expire_date:{token_expire_date}")
                    jrsp, http_status = process_num_nodes_api(name=name, cluster_name=cluster_name, user=user, desired_num_nodes=desired_num_nodes, expire_time=token_expire_date)
            else:
                return Response(jrsp, status=http_status)
        except Exception as e:
            LOG.exception("caught exception:")
            jrsp = {'status': "FAILED", "error_msg": "Server Error"}
            http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
        return Response(jrsp, status=http_status)

class DesiredNumNodesTTLView(generics.CreateAPIView):
    '''
        Takes an name, cluster_name, desired_num_nodes and ttl ("time to live" in minutes) and creates an ClusterNumNode request for the org.
        NOTE: If the ttl is not provided, the time to live will be set the the minimum (15 minutes). The maximum ttl is 720 minutes (i.e. 12 hours).
        This request will go into the pool of requests for the org.
        The msg field of the JSON response will contain the this expiration date/time.
        The current highest number of nodes in the pool of all the requests that are not expired will be deployed to the org. 
        Expired entries are immediately removed from the pool and the number of nodes are adjusted if needed.
    '''
    serializer_class = DummySerializer
    def create(self, request, name, cluster_name, desired_num_nodes, ttl=None, *args, **kwargs):
        try:
            jrsp, http_status, active, user, token_expire_date = get_token_org_active_membership(request, org_name)
            if http_status == status.HTTP_200_OK:
                if active:
                    if ttl is not None and (int(ttl) < MIN_TTL or int(ttl) > MAX_TTL):
                        http_status = status.HTTP_400_BAD_REQUEST
                        max_ttl_hrs = MAX_TTL/60
                        jrsp = {'status': "FAILED","error_msg":f"TTL mins must be greater than or equal to 15 and less than or equal to {MAX_TTL} (i.e. ({max_ttl_hrs} hrs)"}
                    else:
                        orgAccountObj = OrgAccount.objects.get(name=name)
                        clusterObj = NodeGroup.objects.get(org=orgAccountObj,name=cluster_name)
                        if ttl is None:
                            ttl_exp_tm = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=MIN_TTL)
                        else:
                            ttl_exp_tm = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=int(ttl))
                        LOG.info(f"type(ttl_exp_tm):{type(ttl_exp_tm)} ttl_exp_tm:{ttl_exp_tm} ttl:{ttl}")
                        if clusterObj.is_deployed or clusterObj.allow_deploy_by_token:
                            jrsp,http_status = process_num_nodes_api(name=name, cluster_name=cluster_name, user=user, desired_num_nodes=desired_num_nodes, expire_time=token_expire_date)
                        else:
                            jrsp = {'status': "FAILED","error_msg":f"cluster for {orgAccountObj.name} is not deployed and is not configured to be deployed with this request (See admin for details)"}
                            http_status = status.HTTP_503_SERVICE_UNAVAILABLE
                else:
                    http_status = status.HTTP_401_UNAUTHORIZED
                    jrsp = {'status': "FAILED","error_msg":f"{user.username} is Not an Active Member of {org_name}"}           
        except Exception as e:
            LOG.exception("caught exception:")
            jrsp = {'status': "FAILED","error_msg":"Server Error"}
            http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
        return Response(jrsp, status=http_status)

class OrgIpAdrView(generics.RetrieveAPIView):
    '''
        Takes an org_name and returns the IP address of the cluster manager for the org.
    '''
    serializer_class = DummySerializer
    def get(self, request, name, cluster_name, *args, **kwargs):
        try:
            jrsp, http_status, active, user, token_expire_date = get_token_org_active_membership(request, org_name)
            if http_status == status.HTTP_200_OK:
                if active:
                    orgAccountObj = OrgAccount.objects.get(name=name)
                    clusterObj = NodeGroup.objects.get(org=orgAccountObj,name=cluster_name)
                    if clusterObj.is_deployed:
                        jrsp = {'status': "SUCCESS",'ip_address':clusterObj.mgr_ip_address}
                        http_status = status.HTTP_200_OK
                    else:
                        jrsp = {'status': "FAILED","error_msg":f"cluster for {clusterObj} is not deployed."}
                        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
            else:
                return Response(jrsp, status=http_status)
        except Exception as e:
            LOG.exception("caught exception:")
            jrsp = {'status': "FAILED","error_msg":"Server Error"}
            http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
        return Response(jrsp, status=http_status)
    
class RemoveUserNumNodesReqsView(generics.UpdateAPIView):
    '''
        Removes all ClusterNumNode requests for the org from this user from the active pool of requests. 
    '''
    serializer_class = DummySerializer
    def update(self, request, name, cluster_name, *args, **kwargs):
        LOG.info(f"{request.user.username} {name}")
        status = 200
        try:
            orgAccountObj = OrgAccount.objects.get(name=org_name)
        except:
            jrsp = {'status': "FAILED","error_msg":f"Unknown org:{org_name}"}
            status=400
        jrsp,status,active,user,token_expire_date = get_token_org_active_membership(request,org_name)
        if status == 200:
            if active:
                clusterObj = NodeGroup.objects.get(org=orgAccountObj,name=cluster_name)
                jrsp = remove_num_node_requests(request.user,clusterObj,only_owned_by_user=True)
        return Response(jrsp,status = status)


class RemoveAllNumNodesReqsView(generics.UpdateAPIView):
    '''
        Removes all ClusterNumNode requests for the org from the active pool of requests.  Must be a developer or owner of the org to remove ALL the requests.
    '''
    serializer_class = DummySerializer
    def update(self, request, name, cluster_name, *args, **kwargs):
        LOG.info(f"{request.user.username} {name}")
        status = 200
        try:
            orgAccountObj = OrgAccount.objects.get(name=org_name)
        except:
            jrsp = {'status': "FAILED","error_msg":f"Unknown org:{org_name}"}
            status=400
        jrsp,status,active,user,token_expire_date = get_token_org_active_membership(request,org_name)
        if status == 200:
            if active:
                jrsp,status,user_in_token = get_user_in_token(request)
                if user_in_token is not None:
                    clusterObj = NodeGroup.objects.get(org=orgAccountObj,name=cluster_name)
                    if user_in_token.groups.filter(name='PS_Developer').exists() or clusterObj.owner==user_in_token:
                        jrsp = remove_num_node_requests(request.user,clusterObj,only_owned_by_user=False)
                    else:
                        status = 400
                        jrsp = {'status': "FAILED","error_msg":f"{user_in_token.username} is not an admin of {org_name}"}
                else:
                    status = 400
                    jrsp = {'status': "FAILED","error_msg":"Invalid Token (invalid user in token)"}
            else:
                status = 401
                jrsp = {'status': "FAILED","error_msg":f"{user.username} is Not an Active Member of {org_name}"}
        return Response(jrsp,status = status)

class ClusterConfigView(generics.UpdateAPIView):
    '''
    Takes an org_name cluster_name min_nodes and max_nodes and updates the cluster's min and max nodes.
    These are the limits that regular users can request.
    NOTE: Must be a developer or owner of the org to update the config.
    '''
    serializer_class = DummySerializer

    def update(self, request, org_name, cluster_name, min_nodes, max_nodes, *args, **kwargs):

        LOG.info(f"{request.user.username} {org_name} {cluster_name} min:{min_nodes} max:{max_nodes}")

        try:
            clusterObj = NodeGroup.objects.get(name=cluster_name,org_name=org_name)
            orgAccountObj = clusterObj.org
        except:
            jrsp = {'status': "FAILED","error_msg":f"Unknown :{org_name} {cluster_name}"}
            return Response(jrsp, status=status.HTTP_400_BAD_REQUEST)

        jrsp, http_status, active, user, token_expire_date = get_token_org_active_membership(request, org_name)
        if http_status == status.HTTP_200_OK:
            if active:
                if user is not None:
                    if user.groups.filter(name='PS_Developer').exists() or orgAccountObj.owner==user:
                        clusterObj = NodeGroup.objects.get(org=orgAccountObj,name=cluster_name)
                        LOG.info(f'configuring min-max nodes for {clusterObj} {user.username} ')
                        error_msg = ''
                        if max_nodes > 0 and max_nodes <= clusterObj.admin_max_node_cap:
                            if min_nodes >= 0 and min_nodes <= max_nodes:
                                clusterObj.cfg_asg.min = min_nodes
                                clusterObj.cfg_asg.max = max_nodes
                                clusterObj.provision_env_ready = False # force a SetUp i.e. configure
                                clusterObj.save(update_fields=['min_node_cap', 'max_node_cap'])
                                jrsp = {'status': "SUCCESS","msg":f"updated min-max nodes for {name} {user.username} to {clusterObj.cfg_asg.min}-{clusterObj.cfg_asg.max}"}
                            else:
                                http_status = status.HTTP_400_BAD_REQUEST
                                error_msg = f"INVALID min_nodes provided:{min_nodes} must be >= 0 and <= max_node given (i.e. {max_nodes}) and <= {clusterObj.admin_max_node_cap}"
                        else:
                            http_status = status.HTTP_400_BAD_REQUEST
                            error_msg = f"INVALID max_nodes provided:{max_nodes} must be > 0 and <= {clusterObj.admin_max_node_cap}"
                        if error_msg != '':
                            jrsp = {'status': "FAILED","error_msg":f"{error_msg}"}
                    else:
                        http_status = status.HTTP_400_BAD_REQUEST
                        jrsp = {'status': "FAILED","error_msg":f"{user.username} is not an admin of {org_name}"}
                else:
                    http_status = status.HTTP_400_BAD_REQUEST
                    jrsp = {'status': "FAILED","error_msg":f"Invalid user"}
            else:
                http_status = status.HTTP_400_BAD_REQUEST
                jrsp = {'status': "FAILED","error_msg":f"{user.username} is not an active member of {org_name}"}
        return Response(jrsp, status=http_status)
    

class NumNodesView(generics.RetrieveAPIView):
    '''
    Takes an org_name cluster_name and returns the min-current-max number of nodes and the version from the cluster for the org.
    '''
    serializer_class = DummySerializer
    def get(self, request, name, cluster_name, *args, **kwargs):
        try:
            orgAccountObj = OrgAccount.objects.get(name=name)
            clusterObj = NodeGroup.objects.get(org=orgAccountObj,name=cluster_name)
        except:
            jrsp = {'status': "FAILED","error_msg":f"Unknown org:{org_name}"}
            return Response(jrsp, status=status.HTTP_400_BAD_REQUEST)        
        jrsp, http_status, active, user, token_expire_date = get_token_org_active_membership(request, org_name)
        if http_status == status.HTTP_200_OK:
            if active:
                try:
                    update_cur_num_nodes(clusterObj)
                    if clusterObj.is_deployed:
                        jrsp = {'status': "SUCCESS",'min_nodes':clusterObj.cur_asg.min,'current_nodes': clusterObj.cur_asg.num, 'max_nodes':clusterObj.cur_asg.max, 'version':clusterObj.cur_version}
                    else:
                        jrsp = {'status': "SUCCESS",'min_nodes':clusterObj.cur_asg.min,'current_nodes': 0, 'max_nodes':clusterObj.cur_asg.max, 'version':clusterObj.cur_version}
                    http_status = status.HTTP_200_OK
                except:
                    LOG.exception("caught exception:")
                    jrsp = {'status': "FAILED","error_msg":"server Error"}
                    http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            else:
                jrsp = {'status': "FAILED","error_msg":"user is not an ACTIVE member?"}
                http_status=status.HTTP_400_BAD_REQUEST
        return Response(jrsp, status=http_status)

class DisableProvisioningSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()
    mfa_code = serializers.CharField()

@extend_schema(
    exclude=True
)
class DisableProvisioningView(generics.UpdateAPIView):
    '''
    USED FOR PROVISIONING SYSTEM DEVELOPMENT ONLY!
    Takes a username, password and mfa_code and disables provisioning for ALL clusters in the domain.
    This is used when provisioning a new Provisioining System cluster.
    This can only be done by a PS_Developer. 
    Once this endpoint is called, the PS_Developer will need to re-deploy the provisioning system cluster in order for the provisioning system to accept any new provisioning requests.
    '''
    serializer_class = DisableProvisioningSerializer

    def update(self, request, *args, **kwargs):
        username = request.data.get('username')
        password = request.data.get('password')
        mfa_code = request.data.get('mfa_code')

        if not all([username, password, mfa_code]):
            return Response({'status': "FAILED","error_msg":"Missing required fields"}, status=status.HTTP_400_BAD_REQUEST)

        user = authenticate(request, username=username, password=password)
        if user is not None:
            if user.groups.filter(name='PS_Developer').exists():
                login(request, user)
                try:
                    if mfa_code == os.environ.get('MFA_CODE'):
                        set_PROVISIONING_DISABLED('True')
                    else:
                        return Response({'status': "FAILED","error_msg":"Invalid MFA code"}, status=status.HTTP_400_BAD_REQUEST)
                except Exception as e:
                    return Response({'status': "FAILED","error_msg":f"Failed to disable provisioning: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
                jrsp = {'status': "SUCCESS","msg":"You have disabled provisioning! Re-Deploy required!"}
                http_status=status.HTTP_200_OK
            else:
                jrsp = {'status': "FAILED","error_msg":"User is not a PS_Developer"}
                http_status=status.HTTP_400_BAD_REQUEST
        else:
            jrsp = {'status': "FAILED","error_msg":"Invalid username/password"}
            http_status=status.HTTP_400_BAD_REQUEST
        LOG.info(f"{jrsp}")
        return Response(jrsp, status=http_status)

class OrgTokenObtainPairView(TokenObtainPairView):
    '''
    Takes a set of user credentials along with an organization and returns an access and refresh JSON web if the user is an active member of that organization.
    The Access token will contain the organization name and the user name in the claims and expire in 1 hour.
    The Refresh token will expire in 1 day. A Refresh token can be used with the /org_token/refresh/ endpoint to obtain a new Access token.
    '''
    serializer_class = OrgTokenObtainPairSerializer

class OrgTokenRefreshView(TokenRefreshView):
    '''
    Takes a refresh type JSON web token and returns a new access type JSON web token if the refresh token is valid.
    '''
    serializer_class = OrgTokenRefreshSerializer


