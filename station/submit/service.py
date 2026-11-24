"""Report-keyed submissions layered on the active work file.

The bookkeeping lives in the capture ledger so that a receipt line and the
report key it carries are committed by the same SQLite transaction as the
batch header and the capture checkpoint. The row is planned before any byte is
written, so a process that dies mid-append can still be reconciled against the
declared original.
"""
import os

from station.recorder.format import parse_gust_line
from station.submit.store import (clear_span, delete_record, load_record, load_records,
                                  mark_complete, plan_original, state_from,
                                  validate_report_key)


def _load(capture, report_key):
    with capture.connect_read() as db:
        return load_record(db, report_key)


def _clear(capture, report_key):
    with capture.transaction() as db:
        clear_span(db, report_key)


def _records(capture):
    with capture.connect_read() as db:
        return load_records(db)


def submit_report(gate, workfile, catalog, capture, report_key, raw):
    validate_report_key(report_key)
    parse_gust_line(raw)
    with gate.hold():
        record = _load(capture, report_key)
        if record is not None and record.raw != raw:
            raise ValueError(f"report key {report_key!r} already holds another original")
        if record is None or record.known_span is None:
            volume = catalog.active().volume
            start = workfile.size()
            with capture.transaction() as db:
                plan_original(db, report_key, raw, volume, start)
            try:
                written = workfile.append_bytes(raw)
            except BaseException:
                if record is None:
                    _delete(capture, report_key)
                else:
                    _clear(capture, report_key)
                raise
            if written != (start, start + len(raw)):
                _rewrite_plan(capture, report_key, raw, volume, written[0])
            else:
                with capture.transaction() as db:
                    mark_complete(db, report_key)
    with capture.connect_read() as db:
        return state_from(db, report_key)


def _rewrite_plan(capture, report_key, raw, volume, start):
    with capture.transaction() as db:
        plan_original(db, report_key, raw, volume, start)
        mark_complete(db, report_key)


def _delete(capture, report_key):
    with capture.transaction() as db:
        delete_record(db, report_key)


def submission_state(capture, report_key):
    validate_report_key(report_key)
    with capture.connect_read() as db:
        return state_from(db, report_key)


def reconcile_submissions(gate, workfile, catalog, capture):
    """Repair report-key bookkeeping after an interrupted tagged append.

    A complete original keeps the volume and byte range where it was first
    written, through reopen, rotation and resubmission. An incomplete original
    still at the end of the active work file whose bytes are a prefix of the
    declared line is completed in place, so it occupies the bytes it first
    used. Any other residue is removed; the key keeps its declared bytes for
    duplicate detection, and a same-bytes resubmission then writes a fresh
    original. Bytes are inspected only through the work-file gate.
    """
    with gate.hold():
        active = catalog.active().volume
        truncate_at = _detach_foreign_records(capture, catalog, workfile, active)
        if truncate_at is not None:
            os.truncate(workfile.path, truncate_at)
            _clear_from(capture, active, truncate_at)
        _finish_tail_record(capture, workfile, active)


def _active_records(capture, active):
    return sorted((record for record in _records(capture)
                   if record.volume == active and record.start is not None),
                  key=lambda record: record.start)


def _detach_foreign_records(capture, catalog, workfile, active):
    truncate_at = None
    for record in _records(capture):
        if record.volume is None:
            continue
        if record.volume != active:
            if not _sealed_holds(catalog, record):
                _clear(capture, record.report_key)
            continue
        size = workfile.size()
        if record.start >= size:
            _clear(capture, record.report_key)
            continue
        data = workfile.read_bytes(record.start, min(record.end, size))
        if record.end <= size:
            if data != record.raw:
                _clear(capture, record.report_key)
            elif not record.complete:
                _mark_complete(capture, record.report_key)
        elif not data or not record.raw.startswith(data):
            _clear(capture, record.report_key)
            if truncate_at is None or record.start < truncate_at:
                truncate_at = record.start
    return truncate_at


def _clear_from(capture, active, offset):
    for record in _records(capture):
        if record.volume == active and record.start is not None and record.start >= offset:
            _clear(capture, record.report_key)


def _finish_tail_record(capture, workfile, active):
    size = workfile.size()
    for record in reversed(_active_records(capture, active)):
        if record.complete or record.end <= size:
            continue
        data = workfile.read_bytes(record.start, size)
        if data and record.raw.startswith(data) and record.start + len(data) == size:
            workfile.append_bytes(record.raw[len(data):])
            _mark_complete(capture, record.report_key)


def _mark_complete(capture, report_key):
    with capture.transaction() as db:
        mark_complete(db, report_key)


def _sealed_holds(catalog, record):
    entry = catalog.lookup(record.volume)
    return (entry is not None and not entry.is_active and record.complete
            and record.end <= entry.end)
