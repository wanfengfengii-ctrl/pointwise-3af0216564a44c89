from station.capture.receipts import get_batch
from station.database import valid_id
from station.review.summary import summarize_shift


class InvalidParents(ValueError):
    pass


def issue_report(capture_store, report_store, parent_ids):
    try:
        parents = tuple(parent_ids)
    except TypeError as error:
        raise InvalidParents("expected an ordered list of receipt IDs") from error
    if not parents or not all(valid_id(parent) for parent in parents):
        raise InvalidParents("a shift report needs at least one valid receipt ID")
    if len(set(parents)) != len(parents):
        raise InvalidParents("receipt IDs must be unique within a report")
    try:
        batches = tuple(get_batch(capture_store, parent) for parent in parents)
    except KeyError as error:
        raise InvalidParents(f"unknown receipt {error.args[0]}") from error
    return report_store.save(parents, summarize_shift(batches))
