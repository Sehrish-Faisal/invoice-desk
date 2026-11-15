from evaluate_corpus import compare,failed_checks

def test_missing_expected_fields_are_not_counted_as_correct():
    expected={'tax':None,'supplier_name':'Example Ltd','lines':[{'quantity':None,'page':1}]}
    checks=compare({},expected)
    assert not any(checks.values())
def test_null_and_decimal_comparisons():
    expected={'tax':None,'declared_total':'25.50','supplier_name':'Example Ltd','lines':[]}
    actual={'tax':None,'declared_total':25.5,'supplier_name':'  EXAMPLE  LTD ','lines':[]}
    assert all(compare(actual,expected).values())
    actual['tax']='0';assert not compare(actual,expected)['tax']
def test_provider_failures_keep_all_expected_fields_in_denominator():
    expected={'tax':None,'supplier_name':'Example Ltd','lines':[{'quantity':None,'page':1}]}
    assert len(failed_checks(expected))==5
    assert not any(failed_checks(expected).values())
