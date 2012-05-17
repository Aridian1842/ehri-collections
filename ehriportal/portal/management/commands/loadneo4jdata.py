"""
Override loaddata so it disconnects Haystack signals before running.
"""
import os
import sys
import json
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.commands.loaddata import Command as LoadDataCommand

from bulbs import neo4jserver

# FIXME: Do away with this global somehow
GRAPH = neo4jserver.Graph() # FIXME: Handle non-default config


class Command(BaseCommand):

    args = "fixture [fixture ...]"
    option_list = BaseCommand.option_list + ()

    def handle(self, *fixture_labels, **options):
        verbosity = int(options.get('verbosity'))
        show_traceback = options.get('traceback')        

        if not len(fixture_labels):
            self.stderr.write(
                self.style.ERROR("No database fixture specified. Please provide "
                    "the path of at least one fixture in the command line.\n")
            )
            return

        fixture_count = 0
        loaded_object_count = 0
        fixture_object_count = 0
        models = set()

        scripts_file = os.path.join(settings.PROJECT_ROOT, "portal", "gremlin.groovy")
        GRAPH.client.scripts.update(scripts_file)

        for fixture in fixture_labels:
            objdata = []
            with open(fixture, "r") as fh:
                objects = json.load(fh)
                for object in objects:
                    data = object["fields"]
                    data["element_type"] = "repository"
                    objdata.append(data)
            params = dict(dataitems=objdata,index_name="repository",keys=None)
            script = GRAPH.client.scripts.get("create_multiple_indexed_vertex")
            print GRAPH.client.gremlin(script, params).content
                    
