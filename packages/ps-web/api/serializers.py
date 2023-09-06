from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from users.models import Membership, OrgAccount, User, NodeGroup
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
#from django.contrib.auth.models import update_last_login
from rest_framework.exceptions import ValidationError
from api.tokens import OrgRefreshToken
from rest_framework_simplejwt.backends import TokenBackend
from datetime import datetime,timezone
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model

import logging

LOG = logging.getLogger('django')

TM_FMT = "%Y-%m-%dT%H:%M:%S%Z"
class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = '__all__'


class OrgAccountSerializer(serializers.ModelSerializer):
    owner = UserSerializer(many=False)

    class Meta:
        model = OrgAccount
        fields = '__all__'


class MembershipSerializer(serializers.ModelSerializer):
    user = UserSerializer(many=False)
    org = OrgAccountSerializer()

    class Meta:
        model = Membership
        fields = '__all__'


class ClusterSerializer(serializers.ModelSerializer):
    org = OrgAccountSerializer()

    class Meta:
        model = NodeGroup
        fields = '__all__'


class OrgTokenObtainPairSerializer(TokenObtainPairSerializer):
    token_class = OrgRefreshToken

    def __init__(self, *args, **kwargs):
        try:
            super().__init__(*args, **kwargs)
            #LOG.info(args)
            #LOG.info(kwargs)
            self.fields['org_name'] = serializers.CharField()

        except KeyError:
            LOG.exception('reading kwargs')
            pass

    @classmethod
    def get_token(cls, user, user_name, org_name):
        return cls.token_class.for_user(user, org_name)

    def validate(self, attrs):
        #LOG.info(attrs)
        username = attrs['username']
        org_name = attrs['org_name']
        LOG.info(f"{username} {org_name}")
        data = super(TokenObtainPairSerializer,self).validate(attrs) # skip a level
        LOG.info(f"{username} {org_name} PASSED base validation")
        user = User.objects.get(username=username)
        try:
            orgAccountObj = OrgAccount.objects.get(name=org_name)
        except (OrgAccount.DoesNotExist):
            msg = f"{org_name} is NOT a valid organization name"
            LOG.info(msg)
            raise PermissionDenied(msg)
        try:
            membership  = Membership.objects.filter(org=orgAccountObj).get(user=user)
        except (Membership.DoesNotExist):
            msg = f"{username} is NOT a member of {orgAccountObj.name}"
            LOG.info(msg)
            raise PermissionDenied(msg)

        serializer  = MembershipSerializer(membership, many=False)
        #LOG.info('serializer_data:%s',serializer.data)
        LOG.info(f"active:{serializer.data['active']}")
        if serializer.data['active']:
            refresh = self.get_token(self.user, username, attrs['org_name'])
            valid_data = TokenBackend(algorithm='HS256').decode(str(refresh.access_token), verify=False)         
            #LOG.info("exp:%s",datetime.strftime(datetime.fromtimestamp(float(valid_data['exp']),tz=timezone.utc),format=TM_FMT))
            data["exp"] = datetime.strftime(datetime.fromtimestamp(float(valid_data['exp']),tz=timezone.utc),format=TM_FMT)
            data["access_lifetime"] = str( api_settings.ACCESS_TOKEN_LIFETIME.total_seconds())
            data["refresh_lifetime"] = str( api_settings.REFRESH_TOKEN_LIFETIME.total_seconds())
            data["refresh"] = str(refresh)
            data["access"] = str(refresh.access_token)
            #LOG.info(data)
        else:
            msg = f"{username} is NOT an active Member of {org_name}"
            LOG.info(msg)
            raise PermissionDenied(msg) 
        LOG.info("returning data")
        return data

class OrgTokenRefreshSerializer(TokenRefreshSerializer):
    token_class = OrgRefreshToken

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def get_token(cls, user, user_name, name):
        LOG.info(f"{user_name} {name}")
        return cls.token_class.for_user(user=user, name= name)

    def validate(self, attrs):
        #LOG.info(f"attrs:{attrs}")

        # Always verify=False for HMAC algorithm
        data = TokenBackend(algorithm='HS256').decode(attrs['refresh'], verify=False)

        user = get_user_model().objects.get(username=data['user_name'])
        try:
            orgAccountObj = OrgAccount.objects.get(name=data['org_name'])
            LOG.info(f"Refresh token for {user.username} {orgAccountObj.name}")
        except (OrgAccount.DoesNotExist):
            msg = f"{data['org_name']} is NOT a valid organization name"
            LOG.info(msg)
            raise PermissionDenied(msg)
        try:
            membership  = Membership.objects.filter(org=orgAccountObj).get(user=user)
        except (Membership.DoesNotExist):
            msg = f"{data['user_name']} is NOT a member of {orgAccountObj.name}"
            LOG.info(msg)
            raise PermissionDenied(msg)

        serializer  = MembershipSerializer(membership, many=False)
        #LOG.info('serializer_data:%s',serializer.data)
        LOG.info(serializer.data['active'])
        returned_data = {}
        if serializer.data['active']:
            # these will throw exception if refresh is invalid expired or blacklisted
            super(TokenRefreshSerializer,self).validate(attrs) # throw exception if invalid or blacklisted
            TokenRefreshSerializer.token_class(attrs["refresh"]).blacklist()
            refresh = self.get_token(user=user, user_name=data['user_name'], name=data['org_name'])
            valid_data = TokenBackend(algorithm='HS256').decode(str(refresh.access_token), verify=False)         
            #LOG.info("exp:%s",datetime.strftime(datetime.fromtimestamp(float(valid_data['exp']),tz=timezone.utc),format=TM_FMT))
            returned_data["exp"] = datetime.strftime(datetime.fromtimestamp(float(valid_data['exp']),tz=timezone.utc),format=TM_FMT)
            returned_data["access_lifetime"] = str( api_settings.ACCESS_TOKEN_LIFETIME.total_seconds())
            returned_data["refresh_lifetime"] = str( api_settings.REFRESH_TOKEN_LIFETIME.total_seconds())
            returned_data["refresh"] = str(refresh)
            returned_data["access"] = str(refresh.access_token)
            #LOG.info(data)
        else:
            msg = f"{data['user_name']} is NOT an active Member of {data['org_name']}"
            LOG.info(msg)
            raise PermissionDenied(msg) 

        refresh.set_jti()
        refresh.set_exp()
        refresh.set_iat()

        return returned_data
