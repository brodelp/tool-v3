"""The annotate-and-store pipeline shared by the route and the job dispatcher (§4.8, §4.10)."""

import pytest

from scat_validator.annotation_pipeline import PipelineFailure, annotate_and_store
from scat_validator.api import app
from scat_validator.llm.prompt import FULL_SCOPE
from scat_validator.storage import Storage, StoredSubmission
from tests.conftest import FakeOutput
from tests.helpers import MISSING
from tests.test_annotated_submissions import Install
from tests.test_llm_routes import CASE, OUTPUT, llm_set, record_bytes

USER = "demo_annotator"


async def run(storage: Storage, record: str) -> StoredSubmission | PipelineFailure:
    state = app.state
    return await annotate_and_store(
        storage,
        state.llm_settings,
        state.llm_client,
        state.clock,
        FULL_SCOPE,
        record,
        CASE,
        USER,
    )


def source() -> str:
    return record_bytes(annotation_sets=MISSING).decode()


@pytest.mark.anyio
async def test_success_returns_the_stored_submission(storage: Storage, llm: Install) -> None:
    llm(OUTPUT)
    result = await run(storage, source())
    assert isinstance(result, StoredSubmission)
    assert result.version == 2
    assert storage.fetch(CASE, USER) == result


@pytest.mark.anyio
async def test_output_invalid_carries_its_reason(storage: Storage, llm: Install) -> None:
    altered: FakeOutput = "Aspirin caused <AE>nausea</AE>!"
    llm(altered)
    result = await run(storage, source())
    assert isinstance(result, PipelineFailure)
    assert (result.code, result.reason) == ("LLM_OUTPUT_INVALID", "NARRATIVE_ALTERED")
    assert result.outcome.status == 502
    assert storage.fetch(CASE, USER) is None


@pytest.mark.anyio
async def test_already_stored_skips_the_provider(storage: Storage, llm: Install) -> None:
    fake = llm(OUTPUT)
    record = source()
    storage.insert(CASE, USER, record)
    result = await run(storage, record)
    assert isinstance(result, PipelineFailure)
    assert result.code == "ALREADY_SUBMITTED"
    assert fake.requests == []


@pytest.mark.anyio
async def test_candidate_report_is_a_failure(storage: Storage, llm: Install) -> None:
    sets = {f"llm:other:m{number}": llm_set(f"m{number}", "other") for number in range(16)}
    llm(OUTPUT)
    result = await run(storage, record_bytes(annotation_sets=sets).decode())
    assert isinstance(result, PipelineFailure)
    assert (result.code, result.reason) == ("INVALID_DOCUMENT", None)
    assert result.outcome.status == 422
    assert b'"valid":false' in result.outcome.body.replace(b" ", b"")
