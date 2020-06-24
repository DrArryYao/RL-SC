"""lambda function on AWS Lambda."""
import boto3
from botocore.exceptions import ClientError
from urllib.parse import unquote_plus
from flow.data_pipeline.data_pipeline import AthenaQuery, delete_obsolete_data, update_baseline
from flow.data_pipeline.query import prerequisites, tables,  network_using_edge, summary_tables
from flow.data_pipeline.query import X_FILTER, EDGE_FILTER, WARMUP_STEPS, HORIZON_STEPS

s3 = boto3.client('s3')
queryEngine = AthenaQuery()


def lambda_handler(event, context):
    """Handle S3 put event on AWS Lambda."""

    # stores all lists of completed query for each source_id
    completed = {}

    records = []
    # do a pre-sweep to handle tasks other than initalizing a query
    for record in event['Records']:
        bucket = record['s3']['bucket']['name']
        key = unquote_plus(record['s3']['object']['key'])
        table = key.split('/')[0]
        if table not in tables:
            continue

        # delete unwanted metadata files
        if key[-9:] == '.metadata':
            s3.delete_object(Bucket=bucket, Key=key)
            continue

        # load the partition for newly added table
        query_date = key.split('/')[-3].split('=')[-1]
        partition = key.split('/')[-2].split('=')[-1]
        source_id = "flow_{}".format(partition.split('_')[1])
        if table == "fact_vehicle_trace":
            query_name = "FACT_VEHICLE_TRACE"
        else:
            query_name = partition.replace(source_id, "")[1:]
        queryEngine.repair_partition(table, query_date, partition)

        # delete obsolete data
        if table in summary_tables:
            delete_obsolete_data(s3, key, table)

        # add table that need to start a query to list
        if query_name in prerequisites.keys():
            records.append((bucket, key, table, query_name, query_date, partition, source_id))

    # initialize the queries
    start_filter = WARMUP_STEPS
    stop_filter = WARMUP_STEPS + HORIZON_STEPS
    for bucket, key, table, query_name, query_date, partition, source_id in records:
        if source_id not in completed.keys():
            try:
                completed[source_id] = s3.get_object(Bucket='circles.data.pipeline', Key='lambda_temp/{}'
                                                     .format(source_id))
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchKey':
                    completed[source_id] = []
                else:
                    raise
        completed[source_id].append(query_name)

        metadata_key = "fact_vehicle_trace/date={0}/partition_name={1}/{1}.csv".format(query_date, source_id)
        response = s3.head_object(Bucket=bucket, Key=metadata_key)
        loc_filter = X_FILTER
        if 'network' in response["Metadata"]:
            if response["Metadata"]['network'] in network_using_edge:
                loc_filter = EDGE_FILTER
            if table == 'fact_vehicle_trace' \
                    and 'is_baseline' in response['Metadata'] and response['Metadata']['is_baseline'] == 'True':
                update_baseline(s3, response["Metadata"]['network'], source_id)







        query_dict = tags[table]

        # handle different energy models
        if table == "fact_energy_trace":
            energy_model_id = partition.replace(source_id, "")[1:]
            query_dict = tags[energy_model_id]

        # initialize queries and store them at appropriate locations
        for table_name, query_list in query_dict.items():
            for query_name in query_list:
                result_location = 's3://circles.data.pipeline/{}/date={}/partition_name={}_{}'.format(table_name,
                                                                                                      query_date,
                                                                                                      source_id,
                                                                                                      query_name)
                queryEngine.run_query(query_name,
                                      result_location,
                                      query_date,
                                      partition,
                                      loc_filter=loc_filter,
                                      start_filter=start_filter,
                                      stop_filter=stop_filter)
