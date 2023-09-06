import pytest

from django.utils.timezone import now
from users.tasks import cost_accounting, get_or_create_ClusterNumNodes
from users.models import OrgAccount
from django.test.utils import setup_test_environment
from django.urls import reverse
from datetime import timezone,datetime
from datetime import date, datetime, timedelta, timezone, tzinfo
from users.tests.global_test import GlobalTestCase
from .utilities_for_unit_tests import init_test_environ,random_test_user
import logging
LOG = logging.getLogger('test_console')

class ClusterNumNodesTest(GlobalTestCase):
    def setUp(self):
        #LOG.info(f"{__name__}({self}) ... ------------------")
        return super().setUp()

    def tearDown(self):
        #LOG.info(f"{__name__}({self}) ... ------------------")
        return super().tearDown()
      
    #@pytest.mark.dev
    def test_get_or_create_ClusterNumNodes_001(self):
        orgAccountObj,owner = init_test_environ(ClusterNumNodesTest.test_get_or_create_ClusterNumNodes_001.__name__,
                                                org_owner=None,
                                                max_allowance=20000, 
                                                monthly_allowance=1000,
                                                balance=2000,
                                                fytd_accrued_cost=100, 
                                                most_recent_charge_time=datetime.now(timezone.utc), 
                                                most_recent_credit_time=datetime.now(timezone.utc),
                                                most_recent_recon_time=datetime.now(timezone.utc),
                                                version='latest',
                                                is_public=False)
        test_user = random_test_user()
        test_now = datetime.now(tz=timezone.utc)
        test_expire_date = test_now+timedelta(minutes=16)
        orgNumNode,redundant,msg = get_or_create_ClusterNumNodes(org=orgAccountObj,user=test_user,desired_num_nodes=3,expire_date=test_expire_date)
        self.assertEqual(redundant,False)
        self.assertIsNotNone(orgNumNode)
        #LOG.info(f"------- Done test_get_or_create_ClusterNumNodes_001 -------")
        
class OrgAccountTest(GlobalTestCase):

    def setUp(self):
        #LOG.info(f"{__name__}({self}) ... ------------------")
        pass

    def tearDown(self):
        #LOG.info(f"{__name__}({self}) ... ------------------")
        pass

    #@pytest.mark.dev
    def test_create(self):
        test_user = random_test_user()
        test_org,owner = init_test_environ("TestOrg2",
                                            org_owner=test_user,
                                            max_allowance=20000, 
                                            monthly_allowance=1000,
                                            balance=2000,
                                            fytd_accrued_cost=100, 
                                            most_recent_recon_time=datetime.now(timezone.utc),
                                            version='latest',
                                            is_public=False)
        test_now = datetime.now(tz=timezone.utc)
