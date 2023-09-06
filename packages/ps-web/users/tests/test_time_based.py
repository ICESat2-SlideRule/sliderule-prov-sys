import unittest
import pytest
from users.tests.global_test import GlobalTestCase
from users.tasks import getGranChoice,getFiscalStartDate,reconcile_org,get_org_cost_data,create_forecast
from users.models import OrgAccount,Cost,GranChoice
from datetime import date, datetime, timedelta, timezone, tzinfo
from users.tests.utilities_for_unit_tests import init_test_environ
import time_machine
import json
from django.test.testcases import SerializeMixin
from django.test import TestCase
import ps_server_pb2
import ps_server_pb2_grpc
from users import ps_client
from users.tests.utilities_for_unit_tests import dump_org_account,get_org_dict,random_test_user
from decimal import *
import calendar
from django.test import tag
from users.utils import FULL_FMT
from users.utils import DAY_FMT
from users.utils import MONTH_FMT
from users.global_constants import *

import logging
LOG = logging.getLogger('django')

class TimeTestCaseMixin(SerializeMixin):
    lockfile = __file__
    def setUp(self):
        #LOG.info(f"{__name__}({self}) ... ------------------")
        pass

class TimeBasedTasksTest(TimeTestCaseMixin,TestCase):    
    def test_time_machine_again(self):
        with time_machine.travel(datetime(1985, 10, 26),tick=False):
            # It's the past!
            assert date.today() == date(1985, 10, 26)
        # We've gone back to the future!
        today = date.today()
        ##LOG.info(f" today:{today}")
        assert today > date(2020, 4, 29)


    def test_getFiscalStartDate(self):
        now = datetime.now(timezone.utc)
        #LOG.info(f"real now:{now}")
        with time_machine.travel(datetime(year=2020, month=1, day=26),tick=False):
            # It's the past!
            now = datetime.now(timezone.utc)
            #LOG.info(f"fake now:{now}")
            assert date.today() == date(2020, 1, 26)
            now = datetime.now(timezone.utc)
            fsd = getFiscalStartDate()
            expected_fsd = datetime(year=2019,month=10,day=1,tzinfo=timezone.utc)
            #LOG.info(f"fsd:{fsd} expected_fsd:{expected_fsd}")
            assert fsd == expected_fsd
        # back to the future
        now = datetime.now(timezone.utc)
        #LOG.info(f"real now:{now}")
    
    #@pytest.mark.dev
    @pytest.mark.ps_server_stubbed
    def test_ps_server_stub(self):
        #org = ps_server_pb2.Org(name=TimeBasedTasksTest.test_ps_server_stub.__name__)
        name = TimeBasedTasksTest.test_ps_server_stub.__name__
        st = datetime(year=2022,month=1,day=3)
        start_tm = st.strftime(FULL_FMT)
        #LOG.info(f"start_tm:{start_tm}")
        with ps_client.create_client_channel("account") as channel:
            ac = ps_server_pb2_grpc.AccountStub(channel)
            rsp = ac.DailyHistCost(ps_server_pb2.DailyHistCostReq(name=name, start_tm=start_tm))
            #LOG.info(f"rsp:{rsp}")
    # @pytest.mark.dev
    # @pytest.mark.ps_server_stubbed
    # def test_reconcileOrg(self):
    #     real_now = datetime.now(timezone.utc)
    #     name_prefix=""
    #     #LOG.info(f"real now:{real_now}")
    #     with time_machine.travel(datetime(year=2019, month=1, day=28,hour=11),tick=False):
    #         fake_now1 = datetime.now(timezone.utc)
    #         LOG.info(f"fake now1:{fake_now1}")
    #         orgAccountObj,owner = init_test_environ(name=TimeBasedTasksTest.test_reconcileOrg.__name__,
    #                                                 org_owner=None,
    #                                                 max_allowance=20000, 
    #                                                 monthly_allowance=1000,
    #                                                 balance=2000,
    #                                                 fytd_accrued_cost=100, 
    #                                                 most_recent_recon_time=datetime.now(timezone.utc))
    #         name_prefix = orgAccountObj.name
    #         dump_org_account(orgAccountObj.name)

    #     with time_machine.travel(datetime(year=2020, month=1, day=28,hour=11),tick=False):
    #         fake_now = datetime.now(timezone.utc)
    #         LOG.info(f"fake now:{fake_now}")
    #         orgAccountObj.name= name_prefix+f":Req1:{fake_now}"
    #         orgAccountObj.save()
    #         reconcile_org(orgAccountObj)

    #         org_dict = dump_org_account(orgAccountObj.name)
    #         # TBD convert to using decimal type?
    #         #LOG.info(f"org_dict:{org_dict}")
    #         assert(org_dict['balance'] == '2979.79')
    #         #LOG.info(f"org_dict['fytd_accrued_cost']:{org_dict['fytd_accrued_cost']}")
    #         assert(org_dict['fytd_accrued_cost'] == '20.21')

    #     with time_machine.travel(datetime(year=2020, month=1, day=29,hour=11),tick=False):
    #         fake_now = datetime.now(timezone.utc)
    #         #LOG.info(f"fake now:{fake_now}")
    #         orgAccountObj.name= name_prefix+f":Req2:{fake_now}"
    #         orgAccountObj.save()
    #         reconcile_org(orgAccountObj)

    #         org_dict = dump_org_account(orgAccountObj.name)            
    #         #LOG.info(f"org_dict:{org_dict}")
    #         assert(org_dict['balance'] == '2972.29')
    #         #LOG.info(f"orgAccountObj.fytd_accrued_cost:{orgAccountObj.fytd_accrued_cost}")
    #         assert(org_dict['fytd_accrued_cost'] == '27.71')

    #     with time_machine.travel(datetime(year=2020, month=1, day=30,hour=11),tick=False):
    #         fake_now = datetime.now(timezone.utc)
    #         #LOG.info(f"fake now:{fake_now}")
    #         orgAccountObj.name= name_prefix+f":Req3:{fake_now}"
    #         orgAccountObj.save()
    #         reconcile_org(orgAccountObj)

    #         org_dict = dump_org_account(orgAccountObj.name)
    #         #LOG.info(f"org_dict:{org_dict}")
    #         #LOG.info(f"orgAccountObj.balance:{orgAccountObj.balance} org_dict['balance']:{org_dict['balance']}")
    #         assert(org_dict['balance'] == '2964.79')
    #         #LOG.info(f"orgAccountObj.fytd_accrued_cost:{orgAccountObj.fytd_accrued_cost}")
    #         assert(org_dict['fytd_accrued_cost'] == '35.21')

    #     with time_machine.travel(datetime(year=2021, month=1, day=30,hour=11),tick=False):
    #         fake_now = datetime.now(timezone.utc)
    #        #LOG.info(f"fake now:{fake_now}")
    #         orgAccountObj.name= name_prefix+f":Req4:{fake_now}"
    #         orgAccountObj.save()
    #         reconcile_org(orgAccountObj)

    #         org_dict = dump_org_account(orgAccountObj.name)
    #         #LOG.info(f"org_dict:{org_dict}")
    #         #LOG.info(f"orgAccountObj.balance:{orgAccountObj.balance} org_dict['balance']:{org_dict['balance']}")
    #         assert(org_dict['balance'] == '3957.29')
    #         #LOG.info(f"orgAccountObj.fytd_accrued_cost:{orgAccountObj.fytd_accrued_cost}")
    #         assert(org_dict['fytd_accrued_cost'] == '7.50')

    #     with time_machine.travel(datetime(year=2021, month=1, day=31,hour=11),tick=False):
    #         fake_now = datetime.now(timezone.utc)
    #         #LOG.info(f"fake now:{fake_now}")
    #         orgAccountObj.name= name_prefix+f":Req5:{fake_now}"
    #         orgAccountObj.save()
    #         reconcile_org(orgAccountObj)

    #         org_dict = dump_org_account(orgAccountObj.name)
    #         #LOG.info(f"org_dict:{org_dict}")
    #         #LOG.info(f"orgAccountObj.balance:{orgAccountObj.balance} org_dict['balance']:{org_dict['balance']}")
    #         assert(org_dict['balance'] == '1992.50')
    #         #LOG.info(f"orgAccountObj.fytd_accrued_cost:{orgAccountObj.fytd_accrued_cost}")
    #         assert(org_dict['fytd_accrued_cost'] == '15.00')

    def verify_create_forecast_with_time_of_day(self,inputs,expected):
        recon_tm_offset = inputs['recon_tm_offset']
        cents = Decimal('0.01')
        with time_machine.travel(inputs['time_to_test'],tick=False):
            fake_now1 = datetime.now(timezone.utc)
            #LOG.info(f"fake now1:{fake_now1}")
            # this time must be in the past
            most_recent_recon_time_to_use = fake_now1 - recon_tm_offset
            assert(fake_now1 > most_recent_recon_time_to_use)
            #LOG.info(f"most_recent_recon_time_to_use:{most_recent_recon_time_to_use}")
            #LOG.info(f"expected['most_recent_recon_time_to_use']:{expected['most_recent_recon_time_to_use']}")
            assert(most_recent_recon_time_to_use==expected['most_recent_recon_time_to_use'])
            A_LONG_TIME_FROM_NOW = fake_now1 + timedelta(days=DISPLAY_EXP_TM+DISPLAY_EXP_TM_MARGIN)

            orgAccountObj,owner = init_test_environ(org_owner=random_test_user(),
                                                    max_allowance=20000, 
                                                    monthly_allowance=1000,
                                                    balance=2000,
                                                    fytd_accrued_cost=100, 
                                                    most_recent_recon_time=most_recent_recon_time_to_use)
            test_rate = 0.46
            #LOG.info(f"most_recent_recon_time_to_use:{most_recent_recon_time_to_use}")
            rounded_up_recon_tm_hr = (orgAccountObj.most_recent_recon_time + timedelta(hours=1)).replace(minute=0,second=0,microsecond=0)
            #LOG.info(f"rounded_up_recon_tm_hr:{rounded_up_recon_tm_hr}")
            partial_day = (fake_now1-rounded_up_recon_tm_hr)
            #LOG.info(f"partial_day:{partial_day} partial_day.seconds:{partial_day.seconds}")
            partial_day_whole_hrs = partial_day.seconds/3600 # hrs are 0-23; full hrs only partial is handled below
            #LOG.info(f"partial_day_whole_hrs:{partial_day_whole_hrs}")
            assert(partial_day_whole_hrs == expected['partial_day_whole_hrs'])
            partial_day_hrly_charge = Decimal((partial_day_whole_hrs)*test_rate).quantize(cents, ROUND_HALF_UP)
            partial_day_hrly_charge_str = f"{partial_day_hrly_charge}"
            #LOG.info(f"partial_day_hrly_charge:{partial_day_hrly_charge_str}")

            rounded_up_recon_tm_min = (orgAccountObj.most_recent_recon_time + timedelta(minutes=1)).replace(second=0,microsecond=0)
            partial_hr = (rounded_up_recon_tm_hr-rounded_up_recon_tm_min)
            fractional_hr = partial_hr.seconds/3600.0
            #LOG.info(f"fractional_hr:{fractional_hr} most_recent_recon_time.minute:{orgAccountObj.most_recent_recon_time.minute}")
            partial_hr_mins_charge = Decimal((fractional_hr)*test_rate).quantize(cents, ROUND_HALF_UP)
            partial_hr_mins_charge_str = f"{partial_hr_mins_charge}"
            #LOG.info(f"fractional_hr:{fractional_hr} partial_hr_mins_charge:{partial_hr_mins_charge_str}")
            partial_day_charge = Decimal(partial_day_hrly_charge + partial_hr_mins_charge).quantize(cents, ROUND_HALF_UP)
            #LOG.info(f"partial_day_charge:{partial_day_charge}")
            partial_day_charge_str = f"{partial_day_charge}"
            assert(partial_day_charge_str == expected['partial_day_charge_str'])
            time_to_start =  orgAccountObj.most_recent_recon_time

            weekday,num_days_in_first_month = calendar.monthrange(time_to_start.year, time_to_start.month)
            remainder_days_in_month = (num_days_in_first_month-time_to_start.day)
            partial_month_whole_days = remainder_days_in_month
            #LOG.info(f"partial_month_whole_days:{partial_month_whole_days} expected:{expected['partial_month_whole_days']}")
            assert(partial_month_whole_days == expected['partial_month_whole_days'])

            ddt,hrly_fc,daily_fc,monthly_fc,fc_hourly_tm_bal,fc_daily_tm_bal,fc_monthly_tm_bal = create_forecast( orgAccountObj=orgAccountObj,
                                                                                                                    hourlyRate=test_rate,
                                                                                                                    daily_days_to_forecast=31)
            assert(ddt==A_LONG_TIME_FROM_NOW)
            #LOG.info(f"hrly_fc:{hrly_fc}")
            hrly_fc_dict = json.loads(hrly_fc)
            #LOG.info(f"hrly_fc_dict['tm'][{len(hrly_fc_dict['tm'])-1}]:{hrly_fc_dict['tm'][len(hrly_fc_dict['tm'])-1]}")
            #LOG.info(f"hrly_fc_dict['bal'][{len(hrly_fc_dict['bal'])-1}]:{hrly_fc_dict['bal'][len(hrly_fc_dict['bal'])-1]}")
            assert(hrly_fc_dict['tm'][len(hrly_fc_dict['tm'])-1] == expected['last_hourly_tm'])
            cents = Decimal('0.01')
            final_hrly_decimal_bal = Decimal(hrly_fc_dict['bal'][len(hrly_fc_dict['bal'])-1]).quantize(cents, ROUND_HALF_UP)
            #LOG.info(f"final_hrly_decimal_bal:{final_hrly_decimal_bal}")
            final_hrly_decimal_bal_str = f"{final_hrly_decimal_bal}"
            assert(final_hrly_decimal_bal_str == expected['final_hrly_decimal_bal_str'])
            final = float(orgAccountObj.balance) + float(orgAccountObj.monthly_allowance) - float((len(hrly_fc_dict['tm'])-1)*test_rate) - float(partial_hr_mins_charge)
            #LOG.info(f"final:{final}")
            assert(f"{Decimal(final).quantize(cents, ROUND_HALF_UP)}" == final_hrly_decimal_bal_str)

            #LOG.info(f"daily_fc:{daily_fc}")
            daily_fc_dict = json.loads(daily_fc)
            #LOG.info(f"daily_fc_dict['tm'][{len(daily_fc_dict['tm'])-1}]:{daily_fc_dict['tm'][len(daily_fc_dict['tm'])-1]}")
            #LOG.info(f"daily_fc_dict['bal'][{len(daily_fc_dict['bal'])-1}]:{daily_fc_dict['bal'][len(daily_fc_dict['bal'])-1]}")
            assert(daily_fc_dict['tm'][len(daily_fc_dict['tm'])-1] == expected['last_daily_tm'])
            final_daily_decimal_bal = Decimal(daily_fc_dict['bal'][len(daily_fc_dict['bal'])-1]).quantize(cents, ROUND_HALF_UP) 
            #LOG.info(f"final_daily_decimal_bal:{final_daily_decimal_bal}")
            final_daily_decimal_bal_str = f"{final_daily_decimal_bal}"
            assert(final_daily_decimal_bal_str == expected['final_daily_decimal_bal_str'])
            final = float(orgAccountObj.balance) + float(orgAccountObj.monthly_allowance) - float((len(daily_fc_dict['tm'])-1)*test_rate*24) - float(partial_day_hrly_charge)
            #LOG.info(f"#:{len(daily_fc_dict['tm'])} final:{Decimal(final).quantize(cents, ROUND_HALF_UP)}")
            assert(f"{Decimal(final).quantize(cents, ROUND_HALF_UP)}" == final_daily_decimal_bal_str)


            #LOG.info(f"monthly_fc:{monthly_fc}")
            monthly_fc_dict = json.loads(monthly_fc)
            #LOG.critical(f"fc_monthly_tm_bal:{fc_monthly_tm_bal}")
            #LOG.info(f"monthly_fc_dict['tm'][{len(monthly_fc_dict['tm'])-1}]:{monthly_fc_dict['tm'][len(monthly_fc_dict['tm'])-1]}")
            #LOG.info(f"monthly_fc_dict['bal'][{len(monthly_fc_dict['bal'])-1}]:{monthly_fc_dict['bal'][len(monthly_fc_dict['bal'])-1]}")
            assert(monthly_fc_dict['tm'][len(monthly_fc_dict['tm'])-1] == expected['last_monthly_tm'])
            cents = Decimal('0.01')

            first_month_bal = Decimal(float(orgAccountObj.balance) - (float(partial_month_whole_days*test_rate*24) + float(partial_day_charge))).quantize(cents, ROUND_HALF_UP)
            first_month_bal_str = f"{first_month_bal}"
            #LOG.info(f"partial_month_whole_days:{partial_month_whole_days} partial_day_charge:{partial_day_charge} first_month_bal_str:{first_month_bal_str}")
            assert(first_month_bal_str == expected['first_month_bal_str'])

            month_4 = Decimal(monthly_fc_dict['bal'][4]).quantize(cents, ROUND_HALF_UP) 
            month_3 = Decimal(monthly_fc_dict['bal'][3]).quantize(cents, ROUND_HALF_UP) 
            #LOG.critical(f"month_3:{month_3} month_4:{month_4}")
            diff = month_4 - month_3
            m3 = datetime.strptime(monthly_fc_dict['tm'][3],MONTH_FMT)
            weekday,days_in_month3 = calendar.monthrange(m3.year, m3.month)
            m3_charge = Decimal(days_in_month3*24*test_rate).quantize(cents, ROUND_HALF_UP) 
            #LOG.critical(f"m3:{m3.strftime(FMT_MONTHLY)} days_in_month3:{days_in_month3} test_rate:{test_rate} diff:{diff} m3_charge:{m3_charge} orgAccountObj.monthly_allowance:{orgAccountObj.monthly_allowance}")
            #assert(diff == (orgAccountObj.monthly_allowance - m3_charge))

            hours_in_partial_first_month = (partial_month_whole_days*24) + int(partial_day_whole_hrs)
            hrly_bal_at_first_month_boundary = float(orgAccountObj.monthly_allowance) + float(hrly_fc_dict['bal'][hours_in_partial_first_month])
            #LOG.info(f"hrly_bal_at_month_boudary:{hrly_bal_at_first_month_boundary}")
            last_monthly_decimal_bal = Decimal(monthly_fc_dict['bal'][len(monthly_fc_dict['bal'])-1]).quantize(cents, ROUND_HALF_UP) 
            #LOG.info(f"last_monthly_decimal_bal:{last_monthly_decimal_bal}")
            last_monthly_decimal_bal_str = f"{last_monthly_decimal_bal}"
            assert(last_monthly_decimal_bal_str == expected['last_monthly_decimal_bal_str'])
            final = float(orgAccountObj.balance) + float(orgAccountObj.monthly_allowance*12) - float(365*test_rate*24) - float(partial_month_whole_days*test_rate*24) - float(partial_day_charge)
            #LOG.info(f"#:{len(monthly_fc_dict['tm'])} final:{Decimal(final).quantize(cents, ROUND_HALF_UP)}")
            assert(f"{Decimal(final).quantize(cents, ROUND_HALF_UP)}" == last_monthly_decimal_bal_str)

    #@pytest.mark.dev
    @pytest.mark.cost
    def test_create_forecast_all(self):
        inputs  = dict([
                    ('recon_tm_offset'               , timedelta(seconds=1)),
                    ('time_to_test'                  , datetime(year=2018, month=1, day=21,hour=0,tzinfo=timezone.utc))
        ])
        expected = dict([
                    ('most_recent_recon_time_to_use', datetime(year=2018,month=1,day=20,hour=23,minute=59,second=59,tzinfo=timezone.utc)),
                    ('partial_day_whole_hrs'        , 0 ),
                    ('last_hourly_tm'               , '2018-02-03T23:00'),
                    ('last_hourly_tm'               , '2018-02-03T23:00'),
                    ('final_hrly_decimal_bal_str'   , '2845.90'),
                    ('last_daily_tm'                , '2018-02-20'),
                    ('final_daily_decimal_bal_str'  , '2668.80'),
                    ('last_monthly_tm'              , '2019-01'),
                    ('first_month_bal_str'          , '1878.56'),
                    ('last_monthly_decimal_bal_str' , '9848.96'),
                    ('partial_day_charge_str'       , '0.00'),
                    ('partial_month_whole_days'     , 11),
        ])
        self.verify_create_forecast_with_time_of_day(inputs=inputs,expected=expected)


        inputs  = dict([
                    ('recon_tm_offset'               , timedelta(hours=11,minutes=5, seconds=10)),
                    ('time_to_test'                  , datetime(year=2018, month=1, day=21,hour=0,tzinfo=timezone.utc))
        ])
        expected = dict([
                    ('most_recent_recon_time_to_use', datetime(year=2018,month=1,day=20,hour=12,minute=54,second=50,tzinfo=timezone.utc)),
                    ('partial_day_whole_hrs'        , 11 ),
                    ('last_hourly_tm'               , '2018-02-03T12:00'),
                    ('final_hrly_decimal_bal_str'   , '2845.86'),
                    ('last_daily_tm'                , '2018-02-20'),
                    ('final_daily_decimal_bal_str'  , '2663.74'),
                    ('last_monthly_tm'              , '2019-01'),
                    ('first_month_bal_str'          , '1873.46'),
                    ('last_monthly_decimal_bal_str' , '9843.86'),
                    ('partial_day_charge_str'       , '5.10'),
                    ('partial_month_whole_days'     , 11),
        ])
        self.verify_create_forecast_with_time_of_day(inputs=inputs,expected=expected)

    @pytest.mark.cost
    def verify_get_cost_data(self,inputs,expected):
        with time_machine.travel(inputs['time_to_test'],tick=False):
            fake_now = datetime.now(timezone.utc)
            granObj = getGranChoice(granularity='HOURLY')
            orgCostObj = Cost(org=orgAccountObj, gran=granObj, cost_refresh_time=datetime.now(timezone.utc)-timedelta(weeks=52),tm=datetime.now(timezone.utc))

            orgAccountObj,owner = init_test_environ("TestOrg",
                                                    org_owner=None,
                                                    max_allowance=20000, 
                                                    monthly_allowance=1000,
                                                    balance=2000,
                                                    fytd_accrued_cost=100, 
                                                    most_recent_recon_time=inputs['most_recent_recon_time_to_use'])
            updated = get_org_cost_data(orgAccountObj,'HOURLY',orgCostObj=orgCostObj)

