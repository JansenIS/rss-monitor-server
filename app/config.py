from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    database_url: str = 'postgresql+psycopg2://rss_monitor:change_me@localhost:5432/rss_monitor'
    api_token: str = 'change_this_to_a_long_random_token'

    fetch_concurrency: int = 40
    fetch_timeout_seconds: int = 15
    fetch_interval_seconds: int = 600
    fetch_user_agent: str = 'LocalMediaMonitorRSSServer/0.1 (+self-hosted)'
    max_articles_per_source: int = 200

    server_name: str = 'Local Media Monitor RSS Server'


settings = Settings()
