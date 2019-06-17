import copy
import json
import os
from datetime import datetime

import boto3
from botocore.exceptions import ClientError


class AwsEc2Helper:
    def __init__(self, aws_region_name, vpc_id, instance_ids):
        """
        :type vpc_id: str
        :type aws_region_name: str
        """
        # Load variables from "config.json".
        with open("config.json") as config_file:
            configs = json.load(config_file)

        self.aws_configs = configs["aws"]

        dry_run = configs["dry_run"]
        if isinstance(dry_run, bool):
            self.dry_run = dry_run
        elif isinstance(dry_run, str):
            if configs["dry_run"].capitalize() == "True":
                self.dry_run = True
            elif configs["dry_run"].capitalize() == "False":
                self.dry_run = False
            else:
                raise TypeError("Config value for \"dry_run\" must be a boolean.")
        try:
            if self.aws_configs["use_cli_config"]:
                if self.aws_configs["cli_profile_name"]:
                    session = boto3.Session(profile_name=self.aws_configs["cli_profile_name"])
                    self.ec2_client = session.client("ec2", region_name=aws_region_name)
                else:
                    self.ec2_client = boto3.client("ec2", region_name=aws_region_name)
            elif self.aws_configs["access_key_id"] and self.aws_configs["secret_key"]:
                self.ec2_client = boto3.client("ec2",
                                               aws_access_key_id=self.aws_configs["access_key_id"],
                                               aws_secret_access_key=self.aws_configs["secret_key"],
                                               region_name=aws_region_name)
        except:
            raise AttributeError("Unable to create AWS client. Please verify your \"aws\" settings in \"config.json\".")

        self.backups = dict()
        self.region = aws_region_name
        self.vpc_id = vpc_id
        self.log_path = configs["log_path"]
        self.ec2_resource = boto3.resource("ec2", region_name=aws_region_name)
        self.instance_ids = instance_ids
        self.instances = self.__fetch_instances__()
        self.vpc = self.ec2_resource.Vpc(vpc_id)
        self.cidr_block = self.vpc.cidr_block

        self.route_tables = dict()
        for route_table in self.vpc.route_tables.all():
            self.route_tables[route_table.id] = route_table.__dict__["meta"].data

        self.subnets = dict()
        for subnet in self.vpc.subnets.all():
            self.subnets[subnet.id] = subnet.__dict__["meta"].data

        self.peering_connections = {'accepted': {}, 'requested': {}}
        for connection in self.vpc.accepted_vpc_peering_connections.all():
            data = connection.__dict__["meta"].data
            self.peering_connections["accepted"][connection.id] = data

        for connection in self.vpc.requested_vpc_peering_connections.all():
            data = connection.__dict__["meta"].data
            self.peering_connections["requested"][connection.id] = data

    def __fetch_instances__(self):
        instances = {}
        for instance_id in self.instance_ids:
            instances[instance_id] = self.ec2_resource.Instance(instance_id)
        return instances

    def __getitem__(self, key):
        return self.__dict__[key]

    def __backup_config__(self, primary_key, *secondary_key):
        """
        :type primary_key: str
        :type secondary_key: str
        """

        if secondary_key:
            prop_data = self.__getattribute__(primary_key).__getattribute__(secondary_key)

            if secondary_key in self.backups[primary_key]:
                self.backups[primary_key][secondary_key].push({
                    "timestamp": datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)"),
                    "data": copy.deepcopy(prop_data)})
            else:
                self.backups[primary_key][secondary_key] = [{
                    "timestamp": datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)"),
                    "data": copy.deepcopy(prop_data)}]
        else:
            prop_data = self.__getattribute__(primary_key)

            if primary_key in self.backups:
                self.backups[primary_key].push({
                    "timestamp": datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)"),
                    "data": copy.deepcopy(prop_data)})
            else:
                self.backups[primary_key] = [{
                    "timestamp": datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)"),
                    "data": copy.deepcopy(prop_data)}]

    def to_json(self):
        return json.dumps(self.__dict__)

    def to_json_file(self, output_name):
        """

        :type output_name: str
        """

        try:
            os.stat(self.log_path)
        except OSError:
            try:
                os.mkdir(self.log_path)
            except OSError:
                print("Creation of the directory %s failed" % self.log_path)
            else:
                print("Successfully created the directory %s " % self.log_path)

        output_name = "{}/{}".format(self.log_path, output_name)

        if not output_name.endswith(".json"):
            output_name += ".json"

        print("Exporting to {}".format(output_name))

        data_keys = ["aws_configs", "dry_run", "region", "vpc_id", "owner_id", "cidr_block",
                     "route_tables", "subnets", "peering_connections"]
        json_data = dict()

        for key in data_keys:
            json_data[key] = self[key]

        with open(output_name, "w") as output_file:
            json.dump(json_data, output_file, sort_keys=True, indent=4)

    def get_route_table_by_destination(self, destination_cidr_block):
        """

        :type destination_cidr_block: str
        """

        for table_id in self.route_tables:
            route_table = self.route_tables[table_id]
            for route in route_table["Routes"]:
                if destination_cidr_block == route["DestinationCidrBlock"]:
                    return route_table

    def delete_route_from_table(self, destination_cidr_block, dry_run=None):
        if dry_run is None:
            dry_run = self.dry_run

        self.__backup_config__("route_tables")
        route_table = self.get_route_table_by_destination(destination_cidr_block)
        route = self.ec2_client.Route(route_table["RouteTableId"], destination_cidr_block)
        response = route.delete(DryRun=self.dry_run)
        self.route_tables = self.vpc.route_tables.filter(DryRun=dry_run)

        return response

    def fetch_subnets(self, dry_run=None):
        """
        :param dry_run:
        """
        if dry_run is None:
            dry_run = self.dry_run

        self.__backup_config__("subnets")
        self.subnets = self.vpc.subnets.filter(DryRun=dry_run)

        return self.subnets

    def fetch_peering_connection_by_accepter_vpc_id(self, accepter_vpc_id, dry_run=None):
        """
        :param dry_run:
        :type accepter_vpc_id: str
        """
        if dry_run is None:
            dry_run = self.dry_run

        response = self.ec2_client.describe_vpc_peering_connections(
            DryRun=dry_run,
            Filters=[
                {"Name": "accepter-vpc-info.vpc-id", "Values": [accepter_vpc_id]},
                {"Name": "requester-vpc-info.vpc-id", "Values": [self.vpc_id]},
            ],
            MaxResults=1
        )
        self.__backup_config__("peering_connections", "accepted")
        self.peering_connections.accepted = response["VpcPeeringConnections"][0]

        return self.peering_connections.accepted

    def fetch_peering_connection_by_requester_vpc_id(self, requester_vpc_id, dry_run=None):
        """
        :param dry_run:
        :type requester_vpc_id: str
        """
        if dry_run is None:
            dry_run = self.dry_run

        response = self.ec2_client.describe_vpc_peering_connections(
            DryRun=dry_run,
            Filters=[
                {"Name": "accepter-vpc-info.vpc-id", "Values": [self.vpc_id]},
                {"Name": "requester-vpc-info.vpc-id", "Values": [requester_vpc_id]},
            ],
            MaxResults=1
        )

        self.__backup_config__("peering_connections", "requested")
        self.peering_connections.requested = response["VpcPeeringConnections"][0]

        return self.peering_connections.requested

    def accept_peering_connection(self, requester_vpc_id, dry_run=None):
        """
        :param dry_run:
        :type requester_vpc_id: str
        """
        if dry_run is None:
            dry_run = self.dry_run

        response = self.fetch_peering_connection_by_requester_vpc_id(requester_vpc_id)
        connection_id = response["VpcPeeringConnectionId"]
        connection = self.ec2_client.VpcPeeringConnection(connection_id)
        connection.wait_until_exists()

        return connection.accept(DryRun=dry_run)["VpcPeeringConnection"]

    def delete_peering_connection(self, requester_vpc_id, dry_run=None):
        """
        :param dry_run:
        :type requester_vpc_id: str
        """
        if dry_run is None:
            dry_run = self.dry_run

        response = self.fetch_peering_connection_by_requester_vpc_id(requester_vpc_id)
        connection_id = response["VpcPeeringConnectionId"]
        connection = self.ec2_client.VpcPeeringConnection(connection_id)
        return_data = connection.delete(DryRun=dry_run)  # type: bool
        return return_data

    def request_peering_connection(self, target_vpc_id, target_vpc_region,
                                   dry_run=None, owner_id=os.getenv("AWS_ACCOUNT_ID")):
        """
        :param dry_run:
        :type owner_id: str
        :type target_vpc_id: str
        :type target_vpc_region: str
        """

        if dry_run is None:
            dry_run = self.dry_run

        connection = self.vpc.request_vpc_peering_connection(
            DryRun=dry_run,
            PeerOwnerId=owner_id,
            PeerVpcId=target_vpc_id,
            PeerRegion=target_vpc_region
        )

        connection.wait_until_exists()
        return connection

    def stop_all_instances(self, hibernate=False, dry_run=None, force=False):
        """

        :type hibernate: bool
        :type dry_run: bool
        :type force: bool
        """
        if dry_run is None:
            dry_run = self.dry_run

        result = {}

        for instance_id in self.instances:
            instance = self.ec2_resource.Instance(instance_id)
            self.instances[instance_id] = instance
            try:
                instance.stop(Hibernate=hibernate, DryRun=dry_run, Force=force)
                print("Waiting for {} to stop".format(instance_id))
                instance.wait_until_stopped()
                self.instances[instance_id] = instance
                print("{} has stopped".format(instance_id))

            except ClientError as e:
                print(e)

        return result

    def start_all_instances(self, dry_run=None):
        """

        :type dry_run: bool
        """
        if dry_run is None:
            dry_run = self.dry_run

        for instance_id in self.instances:
            instance = self.ec2_resource.Instance(instance_id)
            self.instances[instance_id] = instance
            try:
                instance.start(DryRun=dry_run)
                print("Waiting for {} to restart".format(instance_id))
                instance.wait_until_running()
                self.instances[instance_id] = instance
                print("{} has started".format(instance_id))

            except ClientError as e:
                print(e)
