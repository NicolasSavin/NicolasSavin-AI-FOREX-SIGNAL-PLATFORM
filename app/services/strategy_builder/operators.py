from .models import RuleOperator

def exists(v): return v is not None and v != '' and v != [] and v != {}
def compare(op:RuleOperator,current,expected):
    if op==RuleOperator.EXISTS: return exists(current)
    if op==RuleOperator.NOT_EXISTS: return not exists(current)
    if op in {RuleOperator.IN,RuleOperator.NOT_IN}: ok=current in (expected if isinstance(expected,list) else [expected]); return ok if op==RuleOperator.IN else not ok
    if op in {RuleOperator.CONTAINS,RuleOperator.NOT_CONTAINS}:
        ok= expected in current if isinstance(current,(list,tuple,set,str)) else False; return ok if op==RuleOperator.CONTAINS else not ok
    if op==RuleOperator.BETWEEN:
        lo,hi=(expected or [None,None])[:2]; return float(lo)<=float(current)<=float(hi)
    if op==RuleOperator.EQ: return current==expected or str(current).upper()==str(expected).upper()
    if op==RuleOperator.NE: return not compare(RuleOperator.EQ,current,expected)
    a=float(current); b=float(expected)
    return {RuleOperator.GT:a>b, RuleOperator.GTE:a>=b, RuleOperator.LT:a<b, RuleOperator.LTE:a<=b}[op]
