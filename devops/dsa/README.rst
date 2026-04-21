========================================
Digital Slide Archive via Docker Compose
========================================

This directory contains a complete docker compose set up for the Digital Slide Archive.

Edit the docker-compose.yml file (or add a docker compose override file) to add mount points for additional data or for exposing additional ports.

Prerequisites
-------------

Before using this, you need both Docker and docker compose.  See the `official installation instructions <https://docs.docker.com/compose/install>`_.

The docker compose file assumes certain file paths.  This has been tested on Ubuntu 20.04.  It will probably work on other Linux variants.

Get the Digital Slide Archive repository::

    git clone https://github.com/DigitalSlideArchive/digital_slide_archive

Hardware Requirements
~~~~~~~~~~~~~~~~~~~~~

The main server has only modest hardware requirements.  It can run in a 2 core, 8 GByte machine (such as an t3.large EC2 instance), but it will perform better with more memory and cores.

Storage requirements are largely driven by the images used and the number of annotations.  For a small test instance, a few GBytes will suffice.  For a deployment with high usage, this will need to be much, much larger.

The worker's requirements are highly dependent on the algorithms being run.  The core HistomicsTK examples do not require a GPU, but benefit for more cores and more memory.  For a small test instance, this can be run on the same machine as the server.  For a serious deployment, multiple workers with GPUs will greatly facilitate some jobs.

Start
-----

Change to the appropriate directory::

    cd digital_slide_archive/devops/dsa/

To get the most recent built docker images, do::

    docker compose pull

If you don't pull the images, the main image will be built in preference to pulling.

To start the Digital Slide Archive (except on Windows)::

    DSA_USER=$(id -u):$(id -g) docker compose up

This uses your current user id so that database files, logs, assetstore files, and temporary files are owned by the current user.  If you omit setting ``DSA_USER``, files may be created owned by root.

The girder instance can now be accessed at http://localhost:8080. By default, it creates an ``admin`` user with a password of ``password``. Note that this example does not add any default tasks or sample files.  You can log in with the admin user and use the Slicer CLI Web plugin settings to add default tasks (e.g., ``dsarchive/histomicstk:latest``).

Windows
~~~~~~~

On Windows, to start the Digital Slide Archive::

    set DSA_USER=1000:1000
    docker compose up

Stop
----

To stop the Digital Slide Archive::

    docker compose down -v

The ``-v`` option removes unneeded temporary docker volumes.

Sample Data
-----------

Sample data can be added after performing ``docker compose up`` by running::

    python3 utils/cli_test.py dsarchive/histomicstk:latest --test

This downloads the HistomicsTK analysis tools, some sample data, and runs nuclei detection on some of the sample data.  You need Python 3.6 or later available and may need to ``pip install girder-client`` before you can run this command.


Development
-----------

You can log into the running ``girder`` or ``worker`` containers by typing::

    docker compose exec girder bash

There are two convenience scripts ``restart_girder.sh`` and ``rebuild_and_restart_girder.sh`` that can be run in the container.

You can develop source code by mounting the source directory into the container.  See the ``docker-compose.yml`` file for details.

If you need to log into the container as the Girder user, type::

    docker compose exec --user $(id -u) girder bash

Technical Details
-----------------

The Digital Slider Archive is built in Girder and Girder Worker.  Here, these are coordinated using docker compose.  There are five containers that are started:

- `Girder <https://girder.readthedocs.io/>`_.  Girder is an asset and user management system.  It handles permissions and serves data via http.

- `MongoDB <https://www.mongodb.com/>`_.  Girder stores settings and information about users and assets in a MongoDB database.

- `Girder Worker <https://girder-worker.readthedocs.io/>`_.  Girder Worker is a task runner based on `Celery <https://celery.readthedocs.io/>`_ that has specific features to get authenticated data from Girder.

- `RabbitMQ <https://www.rabbitmq.com/>`_.  Girder communicates to Girder Worker through a broker.  In this configuration it is RabbitMQ.  Girder Worker can be run on multiple computers communicating with a single broker to distribute processing.

- `Memcached <https://memcached.org/>`_.  Memcached is used to cache data for faster access.  This is used for large tiled images.

The Digital Slide Archive relies on several Girder plugins:

- `large_image <https://github.com/girder/large_image>`_.  This provides a standardized way to access a wide range of image formats.  Images can be handled as multi-resolution tiles.  large_image has numerous tile sources to handle different formats.

- `HistomicUI <https://github.com/DigitalSlideArchive/HistomicsUI>`_.  This provides a user interface to examine and annotate large images.

- `Slicer CLI Web <https://github.com/girder/slicer_cli_web>`_.  This can run processing tasks in Docker containers.  Tasks report their capabilities via the Slicer CLI standard, listing required and optional inputs and outputs.  These tasks can be selected and configured via Girder and HistomicsUI and then run in a distributed fashion via Girder Worker.

Slicer CLI Web runs tasks in Docker containers and is itself running in a Docker container (in Girder for determining options and Girder Worker to run the task).  In order to allow a process in a docker container to create another docker container, the paths the docker executable and communications sockets are mounted from the host to the docker container.

Permissions
-----------

By default, the girder container is run in Docker privileged mode.  This can be reduced to a small set of permissions (see the docker-compose.yml file for details), but these may vary depending on the host system.  If no extra permissions are granted, or if the docker daemon is started with --no-new-privileges, or if libfuse is not installed on the host system, the internal fuse mount will not be started.  This may prevent full functionality with non-filesystem assestores and with some multiple-file image formats.

Customizing
-----------

Since this uses standard docker compose, you can customize the process by creating a ``docker-compose.override.yml`` file in the same directory (or a yaml file of any name and use appropriate ``docker compose -f docker-compose.yml -f <my yaml file> <command>`` command).  Further, if you mount a provisioning yaml file into the docker image, you can customize settings, plugins, resources, and other options.

See the ``docker-compose.yml`` and ``provision.yaml`` files for details.

Example
~~~~~~~

To add some additional girder plugins and mount additional directories for assetstores, you can do something like this:

``docker-compose.override.yml``::

    ---
    version: '3'
    services:
      girder:
        environment:
          # Specify that we want to use the provisioning file
          DSA_PROVISION_YAML: ${DSA_PROVISION_YAML:-/opt/digital_slide_archive/devops/dsa/provision.yaml}
        volumes:
          # Mount the local provisioning file into the container
          - ./provision.local.yaml:/opt/digital_slide_archive/devops/dsa/provision.yaml
          # Also expose a local data mount into the container
          - /mnt/data:/mnt/data

``provision.local.yaml``::

    ---
    # Load some sample data
    samples: True
    # A list of additional pip modules to install
    pip:
      - girder-oauth
      - girder-ldap
    # rebuild the girder web client since we install some additional plugins
    rebuild-client: True
    # List slicer-cli-images to pull and load
    slicer-cli-image:
      - dsarchive/histomicstk:latest
      - girder/slicer_cli_web:small

Using Private Docker Registries for CLI images
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

One of the principal abilities of the Digital Slide Archive is to run algorithms that are packages via Docker and expose their interface via the Slicer Execution Model.  See `HistomicTK <https://github.com/DigitalSlideArchive/HistomicsTK>`_ as an example.

For docker images that are published on public container registries, these can be imported either as part of the provisioning process or via the Slicer CLI Web plugin UI by using the docker image tag (e.g., ``dsarchive/histomcstk:latest``).

Since private registries require authentication, pulling docker images from private registries will not work in the reference deployment without either logging into the running docker container (for both the main Girder container and for any and all girder_worker containers) and authenticating via the ``docker login <private_registry>`` OR by authenticating on the base operating system and passing through the authentication as part of the provisioning process.

An example of passing through the authentication using docker compose is commented in the default docker-compose.yml file.  In this case, use ``docker login`` on the base machine running the DSA and on any worker machines.  Use the appropriate override:

``docker-compose.override.yml``::

    ---
    version: '3'
    services:
      girder:
        environment:
          DOCKER_CONFIG: /.docker
        volumes:
          - /home/<user directory>/.docker:/.docker:ro
      worker:
        environment:
          DOCKER_CONFIG: /.docker
        volumes:
          - /home/<user directory>/.docker:/.docker:ro

Docker images can then be added via the provisioning or via the UI using the appropriate private registry and tag (e.g., ``private_registry:5000/dsarchive/histomicstk:latest`` would pull the image from a registry called ``private_registry`` that serves data on port 5000).

Database Backup
---------------

You may want to periodically back up the database.  The standard ``mongodump`` tool can be used for this via a command line ``docker compose exec mongodb /usr/bin/mongodump --db girder --archive --gzip > dsa_girder.dump.gz``.  Restoring is similar: ``docker compose exec -T mongodb /usr/bin/mongorestore --db girder --archive --gzip < /tmp/dsa_girder.dump.gz``; you may want to add ``--drop`` as flag to the restore process.  See Mongo's official documentation for details.

DICOMweb Import
---------------

DSA can serve whole-slide images stored on a remote `DICOMweb <https://www.dicomstandard.org/using/dicomweb>`_ server (e.g. Google Cloud Healthcare API, DCM4CHEE, Orthanc) without copying files locally.  Images are read on demand via WADO-RS and appear in the Girder UI like any other item.

How it works
~~~~~~~~~~~~

A **DICOMweb assetstore** (provided by ``large-image-source-dicom``, which is already installed) records the base URL of the remote DICOMweb store.  Each series is represented as a Girder item with one file record per DICOM instance.  No image data is stored locally; all pixel data is streamed from the remote server when a tile is requested.

Importing a curated list of series
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When a DICOMweb store contains many slides (potentially millions), the built-in import endpoint is impractical because it performs a full QIDO-RS scan of the entire store.  The utility script ``utils/import_dicomweb_series.py`` solves this by accepting a specific list of series URLs and importing only those — making exactly one WADO-RS metadata call per series instead of enumerating the whole store.

**Input format**

Each URL must be a fully qualified DICOMweb series URL::

    https://<host>/v1/.../dicomWeb/studies/<StudyInstanceUID>/series/<SeriesInstanceUID>

For Google Cloud Healthcare API the URL structure is::

    https://healthcare.googleapis.com/v1/projects/<project>/locations/<location>/datasets/<dataset>/dicomStores/<store>/dicomWeb/studies/<StudyUID>/series/<SeriesUID>

URLs can be passed as positional arguments, read from a file (one per line), or piped via stdin.  Blank lines and lines starting with ``#`` are ignored.  Duplicate URLs and minor typos (e.g. a double slash after the hostname) are handled automatically.

**Authentication**

On Google Cloud infrastructure the script uses `Application Default Credentials <https://cloud.google.com/docs/authentication/application-default-credentials>`_ (ADC) automatically — no key file or token configuration is needed provided the environment has appropriate IAM permissions.  Outside of GCP, pass a short-lived Bearer token with ``--token``::

    --token "$(gcloud auth print-access-token)"

``google-auth`` must be available in the container.  It is included in the default ``pip:`` list in ``provision.yaml``; if you have overridden that file make sure it is present.

On a machine that is not GCP infrastructure, mount host ADC credentials into the container via ``docker-compose.override.yml`` and run ``gcloud auth login --update-adc`` on the host once::

    services:
      girder:
        environment:
          GOOGLE_APPLICATION_CREDENTIALS: /root/.config/gcloud/application_default_credentials.json
        volumes:
          - ${HOME}/.config/gcloud/application_default_credentials.json:/root/.config/gcloud/application_default_credentials.json:ro

**Usage**

Run the script inside the running girder container::

    # Dry-run first to validate URLs without writing to the database
    docker exec dsa-girder-1 python \
        /opt/digital_slide_archive/devops/dsa/utils/import_dicomweb_series.py \
        --dry-run \
        --file /mnt/data/series_list.txt

    # Import into a specific collection and sub-folder, as a specific user
    docker exec dsa-girder-1 python \
        /opt/digital_slide_archive/devops/dsa/utils/import_dicomweb_series.py \
        --file /mnt/data/series_list.txt \
        --folder "collection/Pathology/WSIs/Cohort A" \
        --username admin

    # Import into a user's Private folder (user is created if it does not exist)
    docker exec dsa-girder-1 python \
        /opt/digital_slide_archive/devops/dsa/utils/import_dicomweb_series.py \
        --file /mnt/data/series_list.txt \
        --folder "user/m087494/Private/Project 1" \
        --username m087494

    # Pass URLs directly as arguments
    docker exec dsa-girder-1 python \
        /opt/digital_slide_archive/devops/dsa/utils/import_dicomweb_series.py \
        "https://healthcare.googleapis.com/v1/projects/.../series/1.2.3" \
        "https://healthcare.googleapis.com/v1/projects/.../series/1.2.4"

    # Pipe from stdin
    cat series_list.txt | docker exec -i dsa-girder-1 python \
        /opt/digital_slide_archive/devops/dsa/utils/import_dicomweb_series.py \
        --file -

**Options**

``--file PATH``
    Read URLs from a file.  Use ``-`` for stdin.

``--folder GIRDER_PATH``
    Destination as a Girder resource path.  Two root formats are supported:

    ``collection/<Name>[/<sub> ...]``
        A top-level collection.  Created if absent (requires admin).
        Example: ``collection/Pathology/WSIs/Cohort A``

    ``user/<login>/Public[/<sub> ...]``
    ``user/<login>/Private[/<sub> ...]``
        A folder inside a user's Public or Private home directory.
        The user must already exist (or be created via ``--username``).
        Example: ``user/m087494/Private/Project 1``

    Any intermediate sub-folders are created automatically.
    Default: ``collection/DICOMweb Imports``.

``--username LOGIN``
    Girder admin login to use as the creator of all imported resources
    (collection, folders, items).  Defaults to the first admin user found.

``--token BEARER_TOKEN``
    Static Bearer token for authentication.  If omitted, ADC is used.

``--dry-run``
    Parse and validate URLs without writing anything to the database.

``--refresh-token``
    Update the stored Bearer token in all DICOMweb assetstores and exit.
    No URL arguments are needed.  See Token expiry below.

**What gets created**

For each unique series the script creates:

- A sub-folder named after the StudyInstanceUID under the destination folder.
- A Girder item named after the SeriesInstanceUID, registered as a large image
  so HistomicsUI shows the slide viewer automatically.
- One Girder file record per DICOM instance (SOPInstanceUID), pointing at the
  DICOMweb assetstore.

The DICOMweb assetstore itself (one per backing store URL) is created automatically on the first run if it does not already exist.  The operation is **idempotent** — re-running the same URL list updates existing records rather than creating duplicates.

Token expiry
~~~~~~~~~~~~

GCP Bearer tokens expire after approximately one hour.  The token is stored in
the assetstore document so Girder can authenticate when serving tile and download
requests.  It must be refreshed before it expires, otherwise the slide viewer
will stop loading tiles.

Refresh manually::

    docker exec dsa-girder-1 python \
        /opt/digital_slide_archive/devops/dsa/utils/import_dicomweb_series.py \
        --refresh-token

To refresh automatically, add a host cron entry (runs every 45 minutes)::

    */45 * * * * docker exec dsa-girder-1 python \
        /opt/digital_slide_archive/devops/dsa/utils/import_dicomweb_series.py \
        --refresh-token >> /path/to/digital_slide_archive/devops/dsa/logs/token_refresh.log 2>&1

Refresh logs are written to ``devops/dsa/logs/token_refresh.log``.

Exposing the series list file
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The file must be accessible inside the girder container.  The easiest approach is to mount a host directory via ``docker-compose.override.yml``::

    services:
      girder:
        volumes:
          - /path/on/host:/mnt/data

Then place ``series_list.txt`` in ``/path/on/host/`` and reference it as ``/mnt/data/series_list.txt``.

Limitations
~~~~~~~~~~~

- The DICOMweb assetstore is **read-only** — uploads are not supported.
- All URLs in a single invocation must point to the same DICOMweb store (one assetstore per store).  Split your list by store and run once per store if needed.
- ``--folder user/...`` paths require the user to already exist in Girder (created automatically when ``--username`` names a non-existent user).
- FUSE mount (``girder mount``) must be available for some downstream features; see the Permissions section above.
