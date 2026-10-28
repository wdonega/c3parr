import pytest
from pydantic import BaseModel, Field, SecretStr

from c3parr.core.errors import InvalidInput, Unavailable
from c3parr.core.job import ComponentSpec
from c3parr.providers.base import ProviderInfo, Requirements
from c3parr.providers.factory import AbstractComponentFactoryFactory, ComponentFactory


class KeyedConfig(BaseModel):
    api_key: SecretStr = Field(json_schema_extra={"secret_env": "DEMO_KEY"})
    retries: int = Field(2, ge=0)


INFO = ProviderInfo(id="demo", interface="demo", version="1", config_schema=KeyedConfig)


@pytest.fixture
def factory() -> ComponentFactory[KeyedConfig]:
    f: ComponentFactory[KeyedConfig] = ComponentFactory("demo")
    f.register(INFO, lambda config: config)
    return f


def test_builds_with_secret_from_configuration(factory: ComponentFactory[KeyedConfig]) -> None:
    spec = ComponentSpec(provider="demo", params={"retries": 5})
    built = factory.create(spec, {"DEMO_KEY": "s3"})
    assert built.api_key.get_secret_value() == "s3"
    assert built.retries == 5


def test_unknown_provider_is_invalid_input(factory: ComponentFactory[KeyedConfig]) -> None:
    with pytest.raises(InvalidInput, match="unknown provider"):
        factory.create(ComponentSpec(provider="outro"), {"DEMO_KEY": "s3"})


def test_invalid_param_is_invalid_input(factory: ComponentFactory[KeyedConfig]) -> None:
    with pytest.raises(InvalidInput, match="invalid parameters"):
        factory.create(ComponentSpec(provider="demo", params={"retries": -1}), {"DEMO_KEY": "s3"})


def test_secret_in_dto_is_rejected(factory: ComponentFactory[KeyedConfig]) -> None:
    spec = ComponentSpec(provider="demo", params={"api_key": "vazou"})
    with pytest.raises(InvalidInput, match="secrets"):
        factory.create(spec, {"DEMO_KEY": "s3"})


def test_missing_secret_is_unavailable(factory: ComponentFactory[KeyedConfig]) -> None:
    with pytest.raises(Unavailable, match="DEMO_KEY"):
        factory.create(ComponentSpec(provider="demo"), {})


def test_missing_extra_is_unavailable() -> None:
    f: ComponentFactory[object] = ComponentFactory("demo")
    reqs = Requirements(extras=("modulo_que_nao_existe",))
    f.register(ProviderInfo(id="x", interface="demo", version="1", requirements=reqs), object)
    with pytest.raises(Unavailable, match="extra"):
        f.create(ComponentSpec(provider="x"), {})


def test_factory_factory_is_a_plain_registry(factory: ComponentFactory[KeyedConfig]) -> None:
    registry = AbstractComponentFactoryFactory({"demo": factory})
    assert registry.for_interface("demo") is factory
    with pytest.raises(InvalidInput):
        registry.for_interface("nada")


class Inner(BaseModel):
    url: str = "http://llm"
    api_key: SecretStr | None = Field(None, json_schema_extra={"secret_env": "LLM_KEY"})


class Outer(BaseModel):
    llm: Inner = Field(default_factory=Inner)


def test_nested_secret_is_injected_and_never_accepted_from_the_dto() -> None:
    f: ComponentFactory[Outer] = ComponentFactory("demo")
    f.register(ProviderInfo(id="n", interface="demo", version="1", config_schema=Outer),
               lambda config: config)  # fmt: skip
    built = f.create(ComponentSpec(provider="n", params={"llm": {"url": "http://x"}}),
                     {"LLM_KEY": "s3"})  # fmt: skip
    assert built.llm.url == "http://x"
    assert built.llm.api_key is not None and built.llm.api_key.get_secret_value() == "s3"
    assert f.create(ComponentSpec(provider="n"), {}).llm.api_key is None
    with pytest.raises(InvalidInput, match="secrets"):
        f.create(ComponentSpec(provider="n", params={"llm": {"api_key": "leak"}}), {})
