import unittest
import pytest
import os

from users.tests.global_test import GlobalTestCase
from users.utils import get_cluster_queue_name_str
from users.tests.utilities_for_unit_tests import init_test_environ,get_test_org,get_test_compute_cluster,OWNER_USER,OWNER_EMAIL,OWNER_PASSWORD,random_test_user,pytest_approx,the_TEST_USER,init_mock_ps_server


import logging
logger = logging.getLogger('test_console')


class UtilsTest(GlobalTestCase):
    def setUp(self):
        #logger.info(f"{__name__}({self}) ... ------------------")
        return super().setUp()

    def tearDown(self):
        #logger.info(f"{__name__}({self}) ... ------------------")
        return super().tearDown()   

    def test_get_cluster_queue_name_str(self):
        #logger.info(f"{__name__}({self}) ... ------------------")
        assert(get_cluster_queue_name_str("testit")==f"ps-cmd-testit")