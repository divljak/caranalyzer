def pytest_configure(config):
    config.addinivalue_line("markers", "bot_stress: offline Bosnian/Serbian/Croatian bot stress cases (opt-in: BOT_STRESS=1)")
