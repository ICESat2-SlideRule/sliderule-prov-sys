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
    path('desired_num_nodes/<str:name>/<str:cluster_name>/<int:desired_num_nodes>/', views.DesiredNumNodesView.as_view(),name='put-num-nodes'),
    path('desired_num_nodes_ttl/<str:name>/<str:cluster_name>/<int:desired_num_nodes>/<int:ttl>/', views.DesiredNumNodesTTLView.as_view(), name='post-num-nodes-ttl'),
    path('num_nodes/<str:name>/<str:cluster_name>/', views.NumNodesView.as_view(), name='get-num-nodes'),
    path('org_ip_adr/<str:name>/<str:cluster_name>/', views.OrgIpAdrView.as_view(),name='get-org-ip-adr'),
    path('cluster_config/<str:name>/<str:cluster_name>/<int:min_nodes>/<int:max_nodes>/',views.ClusterConfigView.as_view(), name='cluster-cfg'),
    path('remove_user_num_nodes_reqs/<str:cluster_name>/<str:name>/', views.RemoveUserNumNodesReqsView.as_view(), name='remove-user-num-nodes-reqs'),
    path('remove_all_num_nodes_reqs/<str:cluster_name>/<str:name>/', views.RemoveAllNumNodesReqsView.as_view(), name='remove-all-num-nodes-reqs'),
    path('token/blacklist/', TokenBlacklistView.as_view(), name='token_blacklist'),
    path('disable_provisioning/', views.DisableProvisioningView.as_view(), name='disable-provisioning'),
    re_path(r'^oauth2/', include('oauth2_provider.urls', namespace='oauth2_provider')),
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    path('schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]
