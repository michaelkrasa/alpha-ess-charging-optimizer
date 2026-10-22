from src.manager.plan_insight import explain_plan


def test_summary_uses_import_prices_and_soc_at_interval_end():
    rows=[{'timestamp':'2026-10-23T13:00:00+00:00','grid_charge_kw':8,'soc_percent':53},
          {'timestamp':'2026-10-23T18:45:00+00:00','grid_charge_kw':0,'soc_percent':10}]
    prices=[{'timestamp':'2026-10-23T17:45:00+00:00','import_eur_kwh':.306},
            {'timestamp':'2026-10-23T19:00:00+00:00','import_eur_kwh':.263}]
    result=explain_plan({'outlook_decisions':rows},prices)[0]['text']
    assert '2.0 kWh' in result and '53%' in result
    assert '19:45 (€306/MWh)' in result and '21:00 is 10%' in result
    assert explain_plan(None,prices)==[]
