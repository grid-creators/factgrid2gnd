import json

from flask import Flask, request, jsonify, Response
from flask_cors import CORS

from converter import convert_entities, convert_entities_stream, validate_record, records_to_marc_xml

app = Flask(__name__)
CORS(app)


@app.route('/api/convert', methods=['POST'])
def convert():
    data = request.json
    qids = data.get('qids', [])
    if not qids:
        return jsonify({'error': 'No QIDs provided'}), 400
    # Validate QID format
    for qid in qids:
        if not qid.startswith('Q') or not qid[1:].isdigit():
            return jsonify({'error': f'Invalid QID format: {qid}'}), 400
    source = data.get('source', 'server')
    try:
        result = convert_entities(qids, source=source)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/convert/stream', methods=['GET'])
def convert_stream():
    """SSE endpoint that streams conversion progress and results."""
    qids_param = request.args.get('qids', '')
    qids = [q.strip().upper() for q in qids_param.split(',') if q.strip()]
    if not qids:
        return jsonify({'error': 'No QIDs provided'}), 400
    for qid in qids:
        if not qid.startswith('Q') or not qid[1:].isdigit():
            return jsonify({'error': f'Invalid QID format: {qid}'}), 400

    source = request.args.get('source', 'server')
    field079q_raw = request.args.get('field079q', 'd')
    field079q = [v.strip() for v in field079q_raw.split(',') if v.strip()] or ['d']
    field667a = request.args.get('field667a', 'Historisches Datenzentrum Sachsen-Anhalt')

    def generate():
        for event in convert_entities_stream(qids, source=source, field079q=field079q, field667a=field667a):
            yield f"data: {json.dumps(event)}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/convert/<qid>', methods=['GET'])
def convert_single(qid):
    """Convert a single QID - faster response for incremental display."""
    if not qid.startswith('Q') or not qid[1:].isdigit():
        return jsonify({'error': f'Invalid QID format: {qid}'}), 400
    source = request.args.get('source', 'server')
    try:
        result = convert_entities([qid], source=source)
        if result['records']:
            return jsonify({'record': result['records'][0]})
        elif result['errors']:
            return jsonify({'error': result['errors'][0]['error']}), 400
        else:
            return jsonify({'error': 'No result'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/convert/validate', methods=['POST'])
def convert_validate():
    record = request.json
    if not record:
        return jsonify({'error': 'No record provided'}), 400
    try:
        validation = validate_record(record)
        return jsonify(validation)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/convert/export', methods=['POST'])
def convert_export():
    data = request.json
    records = data.get('records', [])
    if not records:
        return jsonify({'error': 'No records provided'}), 400
    try:
        xml = records_to_marc_xml(records)
        return Response(
            xml,
            mimetype='application/xml',
            headers={'Content-Disposition': 'attachment; filename=gnd_export.mrcx'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
