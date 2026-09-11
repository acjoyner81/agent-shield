"""OpenTelemetry tracing configuration for Dynatrace OTLP HTTP export."""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def initialize_dynatrace_tracing(
    service_name: str,
    dt_otlp_endpoint: str,
    dt_api_token: str,
    environment: str = "development",
):
    """Configure a process-wide tracer provider and return the service tracer."""
    resource = Resource.create(
        attributes={
            "service.name": service_name,
            "deployment.environment": environment,
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=f"{dt_otlp_endpoint.rstrip('/')}/v1/traces",
        headers={"Authorization": f"Api-Token {dt_api_token}"},
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)


def configure_tracing() -> None:
    """Compatibility hook for future environment-driven initialization."""
