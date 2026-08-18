from goblinvest import storage


def test_provision_creates_only_the_user_dir_and_raw_data(client):
    storage.provision_user_storage(7)

    udir = storage.user_dir(7)
    assert udir.is_dir()
    assert storage.raw_data_dir(7).is_dir()
    # The vault and its adjustments folder belong to goblinvest-core, later.
    assert not storage.vault_path(7).exists()
    assert not storage.adjustments_dir(7).exists()
    assert sorted(p.name for p in udir.iterdir()) == ["raw_data"]


def test_provision_is_idempotent(client):
    storage.provision_user_storage(7)
    storage.provision_user_storage(7)
    assert storage.raw_data_dir(7).is_dir()


def test_adjustments_dir_matches_core_default_naming(client):
    # goblinvest-core derives "<stem>_adjustments" beside the vault file.
    vault = storage.vault_path(7)
    assert storage.adjustments_dir(7) == vault.parent / f"{vault.stem}_adjustments"


def test_users_are_separated(client):
    assert storage.user_dir(1) != storage.user_dir(2)
