import pytest

from app_dashboard.scope import Scope


def test_all_scope_has_a_neutral_predicate():
    assert Scope.all().predicate("sub") == ("true", ())


def test_app_scope_uses_a_bound_parameter():
    assert Scope.for_app(7).predicate("sub") == ("sub.app_id = %s", (7,))


@pytest.mark.parametrize("alias", ["sub; drop table apps", "x.y", "UPPER", "1bad"])
def test_scope_rejects_untrusted_aliases(alias):
    with pytest.raises(ValueError, match="Invalid SQL alias"):
        Scope.for_app(1).predicate(alias)
