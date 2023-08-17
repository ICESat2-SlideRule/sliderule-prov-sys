from django.urls import path, include, re_path
from . import views
from .views import OrgTokenObtainPairView,OrgTokenRefreshView
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenBlacklistView
)
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

urlpatterns = [
    path('org_token/', OrgTokenObtainPairView.as_view(),name='org-token-obtain-pair'),
    path('org_token/refresh/', OrgTokenRefreshView.as_view(),name='org-token-refresh'),
    path('membership_status/<str:name>/', views.MembershipStatusView.as_view(),name='get-membership-status'),
    path('desired_num_nodes/<str:name>/<int:desired_num_nodes>/', views.DesiredOrgNumNodesView.as_view(),name='put-org-num-nodes'),
    path('desired_num_nodes_ttl/<str:name>/<int:desired_num_nodes>/<int:ttl>/', views.DesiredOrgNumNodesTTLView.as_view(), name='post-org-num-nodes-ttl'),
    path('num_nodes/<str:name>/', views.OrgNumNodesView.as_view(), name='get-org-num-nodes'),
    path('org_ip_adr/<str:name>/', views.OrgIpAdrView.as_view(),name='get-org-ip-adr'),
    path('org_config/<str:name>/<int:min_nodes>/<int:max_nodes>/',views.OrgConfigView.as_view(), name='org-cfg'),
    path('remove_user_num_nodes_reqs/<str:name>/', views.RemoveUserOrgNumNodesReqsView.as_view(), name='remove-user-org-num-nodes-reqs'),
    path('remove_all_num_nodes_reqs/<str:name>/', views.RemoveAllOrgNumNodesReqsView.as_view(), name='remove-all-org-num-nodes-reqs'),
    path('token/blacklist/', TokenBlacklistView.as_view(), name='token_blacklist'),
    path('disable_provisioning/', views.DisableProvisioningView.as_view(), name='disable-provisioning'),
    re_path(r'^oauth2/', include('oauth2_provider.urls', namespace='oauth2_provider')),
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    path('schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]
