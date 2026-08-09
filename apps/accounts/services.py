from django.db import transaction

from apps.accounts.models import Account


@transaction.atomic
def delete_account(*, account: Account) -> None:
    locked_account = Account.objects.select_for_update().get(pk=account.pk)
    locked_account.delete()
