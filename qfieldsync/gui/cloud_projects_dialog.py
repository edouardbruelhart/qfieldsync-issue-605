# -*- coding: utf-8 -*-
"""
/***************************************************************************
 CloudProjectsDialog
                                 A QGIS plugin
 Sync your projects to QField on android
                             -------------------
        begin                : 2020-07-28
        git sha              : $Format:%H$
        copyright            : (C) 2020 by OPENGIS.ch
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
import functools
import glob
from pathlib import Path
from typing import Optional

from qgis.PyQt.QtCore import pyqtSignal, Qt, QItemSelectionModel, QDateTime
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QToolButton,
    QTableWidgetItem,
    QWidget,
    QCheckBox,
    QHBoxLayout,
    QTreeWidgetItem,
    QFileDialog,
    QMessageBox,
    QAbstractButton,
    QMenu,
    QAction,
)
from qgis.PyQt.QtGui import QIcon, QFont
from qgis.PyQt.QtNetwork import QNetworkReply
from qgis.PyQt.uic import loadUiType

from qgis.core import QgsProject
from qgis.utils import iface

from qfieldsync.core import CloudProject, Preferences
from qfieldsync.core.cloud_api import ProjectTransferType, ProjectTransferrer, QFieldCloudNetworkManager
from qfieldsync.utils.cloud_utils import to_cloud_title
from qfieldsync.gui.qfield_cloud_transfer_dialog import QFieldCloudTransferDialog
from qfieldsync.gui.cloud_login_dialog import CloudLoginDialog


CloudProjectsDialogUi, _ = loadUiType(os.path.join(os.path.dirname(__file__), '../ui/cloud_projects_dialog.ui'))


def select_table_row(func):
    @functools.wraps(func)
    def closure(self, table_widget, row_idx):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            index = table_widget.model().index(row_idx, 0)
            table_widget.setCurrentIndex(index)
            table_widget.selectionModel().select(index, QItemSelectionModel.ClearAndSelect|QItemSelectionModel.Rows)
            return func(self, *args, **kwargs)
        return wrapper

    return closure


class CloudProjectsDialog(QDialog, CloudProjectsDialogUi):
    projects_refreshed = pyqtSignal()

    def __init__(self, network_manager, parent=None, project=None):
        """Constructor."""
        super(CloudProjectsDialog, self).__init__(parent=parent)
        self.setupUi(self)
        self.preferences = Preferences()
        self.network_manager = network_manager
        self.current_cloud_project = project
        self.transfer_dialog = None
        self.project_transfer = None
        self.default_local_dir = '~/qfieldsync/cloudprojects/'

        if not self.network_manager.has_token():
            login_dlg = CloudLoginDialog(self.network_manager, self)
            login_dlg.authenticate()
            login_dlg.authenticated.connect(lambda: self.welcomeLabelValue.setText(self.preferences.value('qfieldCloudLastUsername')))
            login_dlg.authenticated.connect(lambda: self.network_manager.projects_cache.refresh())

        if self.network_manager.has_token():
            self.show_projects()

        self.use_current_project_directory_action = QAction(QIcon(), self.tr('Use Current Project Directory'))
        self.use_current_project_directory_action.triggered.connect(self.on_use_current_project_directory_action_triggered)

        self.createButton.clicked.connect(self.on_create_button_clicked)
        self.refreshButton.clicked.connect(self.on_refresh_button_clicked)
        self.backButton.clicked.connect(self.on_back_button_clicked)
        self.submitButton.clicked.connect(self.on_submit_button_clicked)
        self.logoutButton.clicked.connect(self.on_logout_button_clicked)
        self.projectsTable.cellDoubleClicked.connect(self.on_projects_table_cell_double_clicked)
        self.buttonBox.clicked.connect(self.on_button_box_clicked)
        self.projectsTable.selectionModel().selectionChanged.connect(self.on_projects_table_selection_changed)
        self.localDirButton.clicked.connect(self.on_local_dir_button_clicked)
        self.localDirButton.setMenu(QMenu())
        self.localDirButton.setPopupMode(QToolButton.MenuButtonPopup)
        self.localDirButton.menu().addAction(self.use_current_project_directory_action)

        self.network_manager.projects_cache.projects_started.connect(self.on_projects_cached_projects_started)
        self.network_manager.projects_cache.projects_error.connect(self.on_projects_cached_projects_error)
        self.network_manager.projects_cache.projects_updated.connect(self.on_projects_cached_projects_updated)
        self.network_manager.projects_cache.project_files_started.connect(self.on_projects_cached_project_files_started)
        self.network_manager.projects_cache.project_files_error.connect(self.on_projects_cached_project_files_error)
        self.network_manager.projects_cache.project_files_updated.connect(self.on_projects_cached_project_files_updated)

        if self.current_cloud_project:
            self.show_project_form()


    def on_projects_cached_projects_started(self):
        self.projectsStack.setEnabled(False)
        self.feedbackLabel.setVisible(False)
        self.feedbackLabel.setText('')

        print('on_projects_cached_projects_started')


    def on_projects_cached_projects_error(self, error):
        self.projectsStack.setEnabled(True)
        self.feedbackLabel.setVisible(True)
        self.feedbackLabel.setText(error)

        print('on_projects_cached_projects_error')


    def on_projects_cached_projects_updated(self):
        self.projectsStack.setEnabled(True)
        self.projects_refreshed.emit()
        self.show_projects()

        print('on_projects_cached_projects_updated')



    def on_projects_cached_project_files_started(self, project_id):
        self.projectFilesTab.setEnabled(False)
        self.feedbackLabel.setVisible(False)
        self.feedbackLabel.setText('')

        print('on_projects_cached_project_files_started', project_id)


    def on_projects_cached_project_files_error(self, project_id, error):
        self.projectFilesTab.setEnabled(True)

        if self.current_cloud_project.id != project_id:
            return

        self.feedbackLabel.setText('Obtaining project files list failed: {}'.format(error))
        self.feedbackLabel.setVisible(True)

        print('on_projects_cached_project_files_error', project_id)


    def on_projects_cached_project_files_updated(self, project_id):
        print('on_projects_cached_project_files_updated', project_id)
        if not self.current_cloud_project or self.current_cloud_project.id != project_id:
            return

        self.projectFilesTab.setEnabled(True)

        for file_obj in self.network_manager.projects_cache.find_project(project_id).cloud_files:
            file_item = QTreeWidgetItem(file_obj)

            file_item.setText(0, file_obj['name'])
            file_item.setText(1, str(file_obj['size']))
            file_item.setTextAlignment(1, Qt.AlignRight)
            file_item.setText(2, file_obj['versions'][-1]['created_at'])

            for version_idx, version_obj in enumerate(file_obj['versions'], 1):
                version_item = QTreeWidgetItem(version_obj)

                version_item.setText(0, 'Version {}'.format(version_idx))
                version_item.setText(1, str(version_obj['size']))
                version_item.setTextAlignment(1, Qt.AlignRight)
                version_item.setText(2, version_obj['created_at'])

                file_item.addChild(version_item)

            self.projectFilesTree.addTopLevelItem(file_item)


        print('on_projects_cached_project_files_updated')


    def on_use_current_project_directory_action_triggered(self, _toggled):
        self.localDirLineEdit.setText(QgsProject.instance().homePath())


    def on_button_box_clicked(self, _button: QAbstractButton):
        self.close()


    def on_local_dir_button_clicked(self):
        self.current_cloud_project.local_dir = self.select_local_dir()
        self.localDirLineEdit.setText(self.current_cloud_project.local_dir)
        self.localDirLineEdit.setText(self.current_cloud_project.local_dir)


    def on_logout_button_clicked(self):
        self.logoutButton.setEnabled(False)
        self.feedbackLabel.setVisible(False)

        reply = self.network_manager.logout()
        reply.finished.connect(self.on_logout_reply_finished(reply)) # pylint: disable=no-value-for-parameter


    @QFieldCloudNetworkManager.reply_wrapper
    @QFieldCloudNetworkManager.read_json
    def on_logout_reply_finished(self, reply, payload):
        if reply.error() != QNetworkReply.NoError or payload is None:
            self.feedbackLabel.setText('Logout failed: {}'.format(QFieldCloudNetworkManager.error_reason(reply)))
            self.feedbackLabel.setVisible(True)
            self.logoutButton.setEnabled(True)
            return

        self.projectsTable.setRowCount(0)
        self.network_manager.set_token('')

        self.preferences.set_value('qfieldCloudLastToken', '')

        self.close()


    def on_refresh_button_clicked(self):
        self.network_manager.projects_cache.refresh()


    def show_projects(self):
        self.feedbackLabel.setText('')
        self.feedbackLabel.setVisible(False)

        self.projectsTable.setRowCount(0)
        self.projectsTable.setSortingEnabled(False)

        if not self.network_manager.projects_cache.projects:
            self.network_manager.projects_cache.refresh()
            return

        for cloud_project in self.network_manager.projects_cache.projects:
            count = self.projectsTable.rowCount()
            self.projectsTable.insertRow(count)
            
            item = QTableWidgetItem(cloud_project.name)
            item.setData(Qt.UserRole, cloud_project)
            item.setData(Qt.EditRole, cloud_project.name)

            cbx = QCheckBox()
            cbx.setEnabled(False)
            cbx.setChecked(cloud_project.is_private)
            # # it's more UI friendly when the checkbox is centered, an ugly workaround to achieve it
            cbx_widget = QWidget()
            cbx_layout = QHBoxLayout()
            cbx_layout.setAlignment(Qt.AlignCenter)
            cbx_layout.setContentsMargins(0, 0, 0, 0)
            cbx_layout.addWidget(cbx)
            cbx_widget.setLayout(cbx_layout)
            # NOTE the margin is not updated when the table column is resized, so better rely on the code above
            # cbx.setStyleSheet("margin-left:50%; margin-right:50%;")

            btn_sync = QToolButton()
            btn_sync.setIcon(QIcon(os.path.join(os.path.dirname(__file__), '../resources/cloud.svg')))
            btn_sync.setToolTip(self.tr('Synchronize with QFieldCloud'))
            btn_edit = QToolButton()
            btn_edit.setIcon(QIcon(os.path.join(os.path.dirname(__file__), '../resources/edit.svg')))
            btn_edit.setToolTip(self.tr('Edit project data'))
            btn_delete = QToolButton()
            btn_delete.setIcon(QIcon(os.path.join(os.path.dirname(__file__), '../resources/delete.svg')))
            btn_delete.setToolTip(self.tr('Delete project'))
            btn_widget = QWidget()
            btn_layout = QHBoxLayout()
            btn_layout.setAlignment(Qt.AlignCenter)
            btn_layout.setContentsMargins(0, 0, 0, 0)
            btn_layout.addWidget(btn_sync)
            btn_layout.addWidget(btn_edit)
            btn_layout.addWidget(btn_delete)
            btn_widget.setLayout(btn_layout)

            btn_sync.clicked.connect(self.on_project_sync_button_clicked(self.projectsTable, count)) # pylint: disable=too-many-function-args
            btn_edit.clicked.connect(self.on_project_edit_button_clicked(self.projectsTable, count)) # pylint: disable=too-many-function-args
            btn_delete.clicked.connect(self.onProjectDeleteButtonClicked(self.projectsTable, count)) # pylint: disable=too-many-function-args

            self.projectsTable.setItem(count, 0, item)
            self.projectsTable.setItem(count, 1, QTableWidgetItem(cloud_project.owner))
            self.projectsTable.setCellWidget(count, 2, cbx_widget)
            self.projectsTable.setCellWidget(count, 3, btn_widget)

            font = QFont()

            if cloud_project.is_current_qgis_project:
                font.setBold(True)

            self.projectsTable.item(count, 0).setFont(font)
            self.projectsTable.item(count, 1).setFont(font)

        self.projectsTable.resizeColumnsToContents()
        self.projectsTable.sortByColumn(2, Qt.AscendingOrder)
        self.projectsTable.setSortingEnabled(True)



    def sync(self):
        assert self.current_cloud_project is not None

        if self.current_cloud_project.cloud_files is not None:
            self.sync2()
            return

        reply = self.network_manager.projects_cache.get_project_files(self.current_cloud_project.id)
        reply.finished.connect(lambda: self.sync())


    def sync2(self):
        assert self.current_cloud_project
        assert self.current_cloud_project.cloud_files is not None

        if not self.current_cloud_project.local_dir:
            self.current_cloud_project.local_dir = self.select_local_dir()

        if not self.current_cloud_project.local_dir:
            return

        self.ask_sync_project()


    def select_local_dir(self) -> Optional[str]:
        """
        ```
            if there is saved location for this project id #
                upload all the files (or the missing on the cloud only)
                download all the files (or the missing on the local only) #
                if project is the current one: #
                    reload the project #
            else
                if the cloud project is not empty
                    ask for path that is empty dir
                    download the project there
                else
                    ask for path #
                
                    if path contains .qgs file: #
                        assert single .qgs file #
                        upload all the files #
            
                save the project location #
            
            ask should that project be opened
        ```
        """

        assert self.current_cloud_project
        assert self.current_cloud_project.cloud_files is not None

        local_dir = None
        initial_path = self.localDirLineEdit.text() or str(Path(QgsProject.instance().homePath()).parent) or self.default_local_dir

        # cloud project is empty, you can upload a local project into it
        if len(self.current_cloud_project.cloud_files) == 0:
            while local_dir is None:
                local_dir = QFileDialog.getExistingDirectory(self, 'Upload local project to QFieldCloud', initial_path)

                if local_dir == '':
                    return

                # not the most efficient scaninning twice the whole file tree for .qgs and .qgz, but at least readable
                if len(glob.glob('{}/**/*.qgs'.format(local_dir))) > 1 or len(glob.glob('{}/**/*.qgz'.format(local_dir))) > 1:
                    QMessageBox.warning(None, self.tr('Multiple QGIS projects'), self.tr('When QFieldCloud project has no remote files, the local checkout directory may contains no more than 1 QGIS project.'))
                    local_dir = None
                    continue

                break

        # cloud project exists and has files in it, so checkout in an empty dir
        else:
            while local_dir is None:
                local_dir = QFileDialog.getExistingDirectory(self, 'Save QFieldCloud project at', initial_path)

                if local_dir == '':
                    return

                if len(os.listdir(local_dir)) > 0:
                    QMessageBox.warning(None, self.tr('QFieldSync checkout requires empty directory'), self.tr('When QFieldCloud project contains remote files the checkout destination needs to be an empty directory.'))
                    local_dir = None
                    continue

                break

        return local_dir


    @select_table_row
    def on_project_sync_button_clicked(self, is_toggled):
        self.sync()


    @select_table_row
    def onProjectDeleteButtonClicked(self, is_toggled):
        self.projectsStack.setEnabled(False)

        reply = self.network_manager.delete_project(self.current_cloud_project.id)
        reply.finished.connect(self.onDeleteProjectReplyFinished(reply)) # pylint: disable=no-value-for-parameter


    @select_table_row
    def on_project_edit_button_clicked(self, is_toggled):
        self.show_project_form()


    @QFieldCloudNetworkManager.reply_wrapper
    def onDeleteProjectReplyFinished(self, reply):
        self.projectsStack.setEnabled(True)

        if reply.error() != QNetworkReply.NoError:
            self.feedbackLabel.setText('Project delete failed: {}'.format(QFieldCloudNetworkManager.error_reason(reply)))
            self.feedbackLabel.setVisible(True)
            return

        self.network_manager.projects_cache.refresh()


    def on_projects_table_cell_double_clicked(self, _row, _col):
        self.show_project_form()


    def on_create_button_clicked(self):
        self.projectsTable.clearSelection()
        self.show_project_form()

    
    def show_project_form(self):
        self.projectsStack.setCurrentWidget(self.projectsFormPage)
        self.projectTabs.setCurrentWidget(self.projectFormTab)

        self.projectOwnerComboBox.clear()
        self.projectOwnerComboBox.addItem(self.preferences.value('qfieldCloudLastUsername'), self.preferences.value('qfieldCloudLastUsername'))
        self.projectFilesTree.clear()

        if self.current_cloud_project is None:
            self.projectTabs.setTabEnabled(1, False)
            self.projectTabs.setTabEnabled(2, False)
            self.projectNameLineEdit.setText('')
            self.projectDescriptionTextEdit.setPlainText('')
            self.projectIsPrivateCheckBox.setChecked(True)
            self.localDirLabel.setEnabled(False)
            self.localDirLineEdit.setEnabled(False)
            self.localDirButton.setEnabled(False)
            self.localDirLineEdit.setText('')
        else:
            self.projectTabs.setTabEnabled(1, True)
            self.projectTabs.setTabEnabled(2, True)
            self.projectNameLineEdit.setText(self.current_cloud_project.name)
            self.projectDescriptionTextEdit.setPlainText(self.current_cloud_project.description)
            self.projectIsPrivateCheckBox.setChecked(self.current_cloud_project.is_private)
            self.localDirLabel.setEnabled(True)
            self.localDirLineEdit.setEnabled(True)
            self.localDirButton.setEnabled(True)
            self.localDirLineEdit.setText(self.current_cloud_project.local_dir)
            self.projectUrlLabelValue.setText('<a href="{url}">{url}</a>'.format(url=self.current_cloud_project.url))
            self.createdAtLabelValue.setText(QDateTime.fromString(self.current_cloud_project.created_at, Qt.ISODateWithMs).toString())
            self.updatedAtLabelValue.setText(QDateTime.fromString(self.current_cloud_project.updated_at, Qt.ISODateWithMs).toString())
            self.lastSyncedAtLabelValue.setText(QDateTime.fromString(self.current_cloud_project.updated_at, Qt.ISODateWithMs).toString())

            index = self.projectOwnerComboBox.findData(self.current_cloud_project.owner)
            
            if index == -1:
                self.projectOwnerComboBox.insertItem(0, self.current_cloud_project.owner, self.current_cloud_project.owner)
                self.projectOwnerComboBox.setCurrentIndex(0)

            self.network_manager.projects_cache.get_project_files(self.current_cloud_project.id)


    def on_back_button_clicked(self):
        self.projectsStack.setCurrentWidget(self.projectsListPage)


    def on_submit_button_clicked(self):
        cloud_project_data = {
            'name': self.projectNameLineEdit.text(),
            'description': self.projectDescriptionTextEdit.toPlainText(),
            'owner': self.projectOwnerComboBox.currentData(),
            'private': self.projectIsPrivateCheckBox.isChecked(),
            'local_dir': self.localDirLineEdit.text()
        }

        self.projectsFormPage.setEnabled(False)
        self.feedbackLabel.setVisible(True)

        if self.current_cloud_project is None:
            self.feedbackLabel.setText(self.tr('Creating project…'))
            reply = self.network_manager.create_project(
                cloud_project_data['name'], 
                cloud_project_data['owner'], 
                cloud_project_data['description'], 
                cloud_project_data['private'])
            reply.finished.connect(self.on_create_project_finished(reply, local_dir=cloud_project_data['local_dir'])) # pylint: disable=no-value-for-parameter
        else:
            self.current_cloud_project.update_data(cloud_project_data)
            self.feedbackLabel.setText(self.tr('Updating project…'))

            reply = self.network_manager.update_project(
                self.current_cloud_project.id, 
                self.current_cloud_project.name, 
                self.current_cloud_project.owner, 
                self.current_cloud_project.description, 
                self.current_cloud_project.is_private)
            reply.finished.connect(self.on_update_project_finished(reply)) # pylint: disable=no-value-for-parameter


    @QFieldCloudNetworkManager.reply_wrapper
    @QFieldCloudNetworkManager.read_json
    def on_create_project_finished(self, reply, payload, local_dir=None):
        self.projectsFormPage.setEnabled(True)

        if reply.error() != QNetworkReply.NoError or payload is None:
            self.feedbackLabel.setText('Project create failed: {}'.format(QFieldCloudNetworkManager.error_reason(reply)))
            self.feedbackLabel.setVisible(True)
            return

        # save `local_dir` configuration permanently, `CloudProject` constructor does this for free
        _project = CloudProject({
            **payload,
            'local_dir': local_dir,
        })

        self.projectsStack.setCurrentWidget(self.projectsListPage)
        self.feedbackLabel.setVisible(False)

        self.network_manager.projects_cache.refresh()


    @QFieldCloudNetworkManager.reply_wrapper
    @QFieldCloudNetworkManager.read_json
    def on_update_project_finished(self, reply, payload):
        self.projectsFormPage.setEnabled(True)

        if reply.error() != QNetworkReply.NoError or payload is None:
            self.feedbackLabel.setText('Project update failed: {}'.format(QFieldCloudNetworkManager.error_reason(reply)))
            self.feedbackLabel.setVisible(True)
            return

        self.projectsStack.setCurrentWidget(self.projectsListPage)
        self.feedbackLabel.setVisible(False)

        self.network_manager.projects_cache.refresh()


    def on_projects_table_selection_changed(self, _new_selection, _old_selection):
        if self.projectsTable.selectionModel().hasSelection():
            row_idx = self.projectsTable.currentRow()
            self.current_cloud_project = self.projectsTable.item(row_idx, 0).data(Qt.UserRole)
        else:
            self.current_cloud_project = None


    # TODO move this to a separate class
    def ask_sync_project(self):
        assert self.current_cloud_project is not None, 'No project to download selected'
        assert self.current_cloud_project.local_dir, 'Cannot download a project without `local_dir` properly set'

        dialog_result = QMessageBox.question(
            self, 
            self.tr('Replace QFieldCloud files'), 
            self.tr('Would you like to replace remote QFieldCloud files with the version available locally?'), 
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, 
            QMessageBox.No)

        if dialog_result == QDialogButtonBox.Yes:
            self.sync_project(replace_remote_files=True)
        elif dialog_result == QDialogButtonBox.No:
            self.sync_project(replace_remote_files=False)
        else:
            return


    def sync_project(self, replace_remote_files: bool) -> None:
        assert self.current_cloud_project is not None, 'No project to sync selected'
        assert self.project_transfer is None, 'There is a project currently uploading'

        self.project_transfer = ProjectTransferrer(self.network_manager, self.current_cloud_project, ProjectTransferType.Sync)

        self.transfer_dialog = QFieldCloudTransferDialog(self.project_transfer, self)
        self.transfer_dialog.rejected.connect(self.on_transfer_dialog_rejected)
        self.transfer_dialog.accepted.connect(self.on_transfer_dialog_accepted)
        self.transfer_dialog.open()
        
        self.project_transfer.sync()


    def on_transfer_dialog_rejected(self):
        self.project_transfer.abort_requests()
        self.transfer_dialog.close()
        self.project_transfer = None
        self.transfer_dialog = None


    def on_transfer_dialog_accepted(self):
        QgsProject().instance().reloadAllLayers()

        self.transfer_dialog.close()
        self.project_transfer = None
        self.transfer_dialog = None


        # replace_all = False

        # for path in Path(self.temp_dir.path()).glob('**/*'):
        #     dest_path = Path(self.cloud_project.local_dir + '/' + str(path.relative_to(self.temp_dir.path())))

        #     if dest_path.exists() and replace_all != True:
        #         result = QMessageBox.question(
        #             self,
        #             self.tr('Replace local file'), 
        #             self.tr('Would you like to replace file "%1" with its cloud version?'), 
        #             QMessageBox.Yes | QMessageBox.YesToAll | QMessageBox.No | QMessageBox.NoToAll,
        #             QMessageBox.NoToAll)

        #     else:
        #         shutil.copy(path, dest_path)

