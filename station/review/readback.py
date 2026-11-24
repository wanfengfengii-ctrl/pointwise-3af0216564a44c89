from station.source.evidence import read_evidence


class UnknownReport(KeyError):
    pass


def get_report(store, report_id):
    report = store.load(report_id)
    if report is None:
        raise UnknownReport(report_id)
    return report


def read_peak_evidence(store, source, report_id):
    report = get_report(store, report_id)
    return read_evidence(source, report.summary.peak_spans)
