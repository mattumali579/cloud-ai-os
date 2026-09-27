from cloudos.discord_bot.bot import _chunks


def test_discord_output_is_chunked_below_limit():
    chunks = _chunks("x" * 4500, limit=1900)
    assert [len(chunk) for chunk in chunks] == [1900, 1900, 700]
