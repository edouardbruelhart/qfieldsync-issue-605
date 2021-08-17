# -*- coding: utf-8 -*-

"""
/***************************************************************************
 QFieldSync
                              -------------------
        begin                : 2016
        copyright            : (C) 2016 by OPENGIS.ch
        email                : info@opengis.ch
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

import os
import tempfile
from pathlib import Path
from typing import List, Union

from qgis.core import QgsMapLayer, QgsProject
from qgis.PyQt.QtCore import QCoreApplication

from qfieldsync.libqfieldsync import ProjectConfiguration
from qfieldsync.libqfieldsync.utils.file_utils import get_project_in_folder


def get_project_title(project: QgsProject) -> str:
    """ Gets project title, or if non available, the basename of the filename"""
    if project.title():
        return project.title()
    else:
        return Path(project.fileName()).stem


def open_project(filename: str, filename_to_read: str = None) -> bool:
    project = QgsProject.instance()
    QCoreApplication.processEvents()
    project.clear()
    QCoreApplication.processEvents()

    is_success = project.read(filename_to_read or filename)
    project.setFileName(filename)

    return is_success


def make_temp_qgis_file(project: QgsProject) -> str:
    project_backup_dir = tempfile.mkdtemp()
    original_filename = project.fileName()
    backup_filename = os.path.join(project_backup_dir, f"{project.baseName()}.qgs")
    project.write(backup_filename)
    project.setFileName(original_filename)

    return backup_filename


def import_checksums_of_project(dirname: str) -> List[str]:
    project = QgsProject.instance()
    qgs_file = get_project_in_folder(dirname)
    open_project(qgs_file)
    original_project_path = ProjectConfiguration(project).original_project_path
    open_project(original_project_path)
    return ProjectConfiguration(project).imported_files_checksums


def get_memory_layers(project: QgsProject) -> List[QgsMapLayer]:
    return [
        layer
        for layer in project.mapLayers().values()
        if layer.isValid() and layer.dataProvider().name() == "memory"
    ]


def get_qgis_files_within_dir(dirname: Union[str, Path]) -> List[Path]:
    dirname = Path(dirname)
    return list(dirname.glob("*.qgs")) + list(dirname.glob("*.qgz"))
