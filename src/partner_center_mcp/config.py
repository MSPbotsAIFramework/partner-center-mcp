from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mcp_http_port: int = 8080
    mcp_http_host: str = "0.0.0.0"

    # Overridable in case a sovereign-cloud Partner Center endpoint is ever
    # needed (PRD-21573 design Decisions); no confirmed MSPbots customer
    # needs one today, so the default is the public commercial endpoint.
    partner_center_base_url: str = "https://api.partnercenter.microsoft.com/v1"


def get_settings() -> Settings:
    return Settings()
