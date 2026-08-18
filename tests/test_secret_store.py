from custom_components.enocean_ptm216b.secret_store import IntegrationSecretStore


async def test_hmac_secret_is_256_bits_and_persists_in_private_store(hass):
    first = await IntegrationSecretStore(hass).async_get_or_create()
    second = await IntegrationSecretStore(hass).async_get_or_create()

    assert len(first) == 32
    assert first == second
