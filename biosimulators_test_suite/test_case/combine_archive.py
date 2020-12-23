""" Methods for test cases involving manually curated COMBINE/OMEX archives

:Author: Jonathan Karr <karr@mssm.edu>
:Date: 2020-12-21
:Copyright: 2020, Center for Reproducible Biomedical Modeling
:License: MIT
"""

from ..data_model import (AbstractTestCase, SedTaskRequirements, ExpectedSedReport, ExpectedSedPlot,
                          AlertType, InvalidOuputsException, InvalidOuputsWarning, SkippedTestCaseException,
                          IgnoredTestCaseWarning)
from biosimulators_utils.combine.data_model import CombineArchive, CombineArchiveContentFormatPattern  # noqa: F401
from biosimulators_utils.combine.io import CombineArchiveReader, CombineArchiveWriter
from biosimulators_utils.config import get_config
from biosimulators_utils.report.data_model import ReportFormat
from biosimulators_utils.sedml.io import SedmlSimulationReader, SedmlSimulationWriter
import biosimulators_utils.archive.io
import biosimulators_utils.simulator.exec
import biosimulators_utils.report.io
import abc
import glob
import json
import numpy
import numpy.testing
import os
import re
import shutil
import tempfile
import warnings

__all__ = ['CuratedCombineArchiveTestCase']

EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'examples')


class CuratedCombineArchiveTestCase(AbstractTestCase):
    """ A test case for validating a simulator that involves executing a COMBINE/OMEX archive

    Attributes:
        id (:obj:`str`): id
        name (:obj:`str`): name
        filename (:obj:`str`): path to archive
        task_requirements (:obj:`list` of :obj:`SedTaskRequirements`): list of the required model format and simulation algorithm
            for each task in the COMBINE/OMEX archive
        expected_reports (:obj:`list` of :obj:`ExpectedSedReport`): list of reports expected to be produced by algorithm
        expected_plots (:obj:`list` of :obj:`ExpectedSedPlot`): list of plots expected to be produced by algorithm
        runtime_failure_alert_type (:obj:`AlertType`): whether a run-time failure should be raised as an error or warning
        assert_no_extra_reports (:obj:`bool`): if :obj:`True`, raise an exception if the simulator produces unexpected reports
        assert_no_extra_datasets (:obj:`bool`): if :obj:`True`, raise an exception if the simulator produces unexpected datasets
        assert_no_missing_plots (:obj:`bool`): if :obj:`True`, raise an exception if the simulator doesn't produce the expected plots
        assert_no_extra_plots (:obj:`bool`): if :obj:`True`, raise an exception if the simulator produces unexpected plots
        r_tol (:obj:`float`): relative tolerence
        a_tol (:obj:`float`): absolute tolerence
    """

    def __init__(self, id=None, name=None, filename=None, task_requirements=None, expected_reports=None, expected_plots=None,
                 runtime_failure_alert_type=AlertType.exception,
                 assert_no_extra_reports=False, assert_no_extra_datasets=False, assert_no_missing_plots=False, assert_no_extra_plots=False,
                 r_tol=1e-4, a_tol=0.):
        """
        Args:
            id (:obj:`str`, optional): id
            name (:obj:`str`, optional): name
            filename (:obj:`str`, optional): path to archive
            task_requirements (:obj:`list` of :obj:`SedTaskRequirements`, optional): list of the required model format and simulation algorithm
                for each task in the COMBINE/OMEX archive
            expected_reports (:obj:`list` of :obj:`ExpectedSedReport`, optional): list of reports expected to be produced by algorithm
            expected_plots (:obj:`list` of :obj:`ExpectedSedPlot`, optional): list of plots expected to be produced by algorithm
            runtime_failure_alert_type (:obj:`AlertType`, optional): whether a run-time failure should be raised as an error or warning
            assert_no_extra_reports (:obj:`bool`, optional): if :obj:`True`, raise an exception if the simulator produces unexpected reports
            assert_no_extra_datasets (:obj:`bool`, optional): if :obj:`True`, raise an exception if the simulator produces unexpected datasets
            assert_no_missing_plots (:obj:`bool`, optional): if :obj:`True`, raise an exception if the simulator doesn't produce the expected plots
            assert_no_extra_plots (:obj:`bool`, optional): if :obj:`True`, raise an exception if the simulator produces unexpected plots
            r_tol (:obj:`float`, optional): relative tolerence
            a_tol (:obj:`float`, optional): absolute tolerence
        """
        super(CuratedCombineArchiveTestCase, self).__init__(id, name)
        self.filename = filename
        self.task_requirements = task_requirements or []
        self.expected_reports = expected_reports or []
        self.expected_plots = expected_plots or []

        self.description = self.get_description()

        self.runtime_failure_alert_type = runtime_failure_alert_type
        self.assert_no_extra_reports = assert_no_extra_reports
        self.assert_no_extra_datasets = assert_no_extra_datasets
        self.assert_no_missing_plots = assert_no_missing_plots
        self.assert_no_extra_plots = assert_no_extra_plots
        self.r_tol = r_tol
        self.a_tol = a_tol

    def get_description(self):
        """ Get a description of the case

        Returns:
            :obj:`str`: description of the case
        """
        model_formats = set()
        simulation_algorithms = set()
        for req in self.task_requirements:
            model_formats.add(req.model_format)
            simulation_algorithms.add(req.simulation_algorithm)
        return '{} / {}'.format(', '.join(sorted(model_formats)), ', '.join(sorted(simulation_algorithms)))

    def from_json(self, base_path, filename):
        """ Read test case from JSON file

        Args:
            base_path (:obj:`str`): bath directory for test cases
            filename (:obj:`str`): JSON file relative to :obj:`base_path`

        Returns:
            :obj:`CuratedCombineArchiveTestCase`: this object
        """
        with open(os.path.join(base_path, filename), 'r') as file:
            data = json.load(file)

        self.id = os.path.splitext(filename)[0]
        self.name = data['name']
        self.filename = os.path.join(os.path.dirname(os.path.join(base_path, filename)), data['filename'])

        return self.from_dict(data)

    def from_dict(self, data):
        """ Read test case from dictionary

        Args:
            data (:obj:`dict`): dictionary with test case data

        Returns:
            :obj:`CuratedCombineArchiveTestCase`: this object
        """
        self.task_requirements = []
        for task_req_def in data['taskRequirements']:
            self.task_requirements.append(SedTaskRequirements(
                model_format=task_req_def['modelFormat'],
                simulation_algorithm=task_req_def['simulationAlgorithm'],
            ))

        self.expected_reports = []
        for exp_report_def in data.get('expectedReports', []):
            values = {}
            for key, val in exp_report_def.get('values', {}).items():
                if isinstance(val, list):
                    values[key] = numpy.array(val)
                else:
                    values[key] = {}
                    for k, v in val.items():
                        values[key][tuple(int(index) for index in k.split(","))] = v

            self.expected_reports.append(ExpectedSedReport(
                id=exp_report_def['id'],
                data_sets=set(exp_report_def.get('dataSets', [])),
                points=tuple(exp_report_def['points']),
                values=values,
            ))

        self.expected_plots = []
        for exp_plot_def in data.get('expectedPlots', []):
            self.expected_plots.append(ExpectedSedPlot(
                id=exp_plot_def['id'],
            ))

        self.runtime_failure_alert_type = AlertType(data.get('runtimeFailureAlertType', 'exception'))
        self.assert_no_extra_reports = data.get('assertNoExtraReports', False)
        self.assert_no_extra_datasets = data.get('assertNoExtraDatasets', False)
        self.assert_no_missing_plots = data.get('assertNoMissingPlots', False)
        self.assert_no_extra_plots = data.get('assertNoExtraPlots', False)
        self.r_tol = data.get('r_tol', 1e-4)
        self.a_tol = data.get('a_tol', 0.)

        return self

    def eval(self, specifications):
        """ Evaluate a simulator's performance on a test case

        Args:
            specifications (:obj:`dict`): specifications of the simulator to validate

        Raises:
            :obj:`SkippedTestCaseException`: if the test case is not applicable to the simulator
            :obj:`Exception`: if the simulator did not pass the test case
        """
        # determine if case is applicable to simulator
        for task_reqs in self.task_requirements:
            reqs_satisfied = False
            for alg_specs in specifications['algorithms']:
                if (task_reqs.model_format in set(format['id'] for format in alg_specs['modelFormats'])
                        and task_reqs.simulation_algorithm == alg_specs['kisaoId']['id']):
                    reqs_satisfied = True
                    break

            if not reqs_satisfied:
                raise SkippedTestCaseException('Case requires {} and {}'.format(task_reqs.model_format, task_reqs.simulation_algorithm))

        # create output directory for case
        out_dir = tempfile.mkdtemp()

        # pull image and execute COMBINE/OMEX archive for case
        try:
            biosimulators_utils.simulator.exec.exec_sedml_docs_in_archive_with_containerized_simulator(
                self.filename, out_dir, specifications['image']['url'], pull_docker_image=True)

        except Exception as exception:
            shutil.rmtree(out_dir)
            if self.runtime_failure_alert_type == AlertType.exception:
                raise
            else:
                warnings.warn(str(exception), RuntimeWarning)
                return

        # check expected outputs created
        errors = []

        # check expected outputs created: reports
        if not os.path.isfile(os.path.join(out_dir, get_config().H5_REPORTS_PATH)):
            errors.append('No reports were generated')

        else:
            report_reader = biosimulators_utils.report.io.ReportReader()
            for expected_report in self.expected_reports:
                try:
                    report = report_reader.run(out_dir, expected_report.id, format=ReportFormat.h5)
                except Exception:
                    errors.append('Report {} could not be read'.format(expected_report.id))
                    continue

                missing_data_sets = set(expected_report.data_sets).difference(set(report.index))
                if missing_data_sets:
                    errors.append('Report {} does not contain expected data sets:\n  {}'.format(
                        expected_report.id, '\n  '.join(sorted(missing_data_sets))))
                    continue

                extra_data_sets = set(report.index).difference(set(expected_report.data_sets))
                if extra_data_sets:
                    if self.assert_no_extra_datasets:
                        errors.append('Report {} contains unexpected data sets:\n  {}'.format(
                            expected_report.id, '\n  '.join(sorted(extra_data_sets))))
                        continue
                    else:
                        warnings.warn('Report {} contains unexpected data sets:\n  {}'.format(
                            expected_report.id, '\n  '.join(sorted(extra_data_sets))), InvalidOuputsWarning)

                if report.shape[1:] != expected_report.points:
                    errors.append('Report {} contains incorrect number of points: {} != {}'.format(
                                  expected_report.id, report.shape[1:], expected_report.points))
                    continue

                for data_set_id, expected_value in expected_report.values.items():
                    if isinstance(expected_value, dict):
                        value = report.loc[data_set_id, :]
                        for el_id, expected_el_value in expected_value.items():
                            el_index = numpy.ravel_multi_index([el_id], value.shape)[0]
                            try:
                                numpy.testing.assert_allclose(
                                    value[el_index],
                                    expected_el_value,
                                    rtol=self.r_tol,
                                    atol=self.a_tol,
                                )
                            except AssertionError:
                                errors.append('Data set {} of report {} does not have expected value at {}: {} != {}'.format(
                                    data_set_id, expected_report.id, el_id, value[el_index], expected_el_value))
                    else:
                        try:
                            numpy.testing.assert_allclose(
                                report.loc[data_set_id, :],
                                expected_value,
                                rtol=self.r_tol,
                                atol=self.a_tol,
                            )
                        except AssertionError:
                            errors.append('Data set {} of report {} does not have expected values'.format(
                                data_set_id, expected_report.id))

            report_ids = report_reader.get_ids(out_dir)
            expected_report_ids = set(report.id for report in self.expected_reports)
            extra_report_ids = report_ids.difference(expected_report_ids)
            if extra_report_ids:
                if self.assert_no_extra_reports:
                    errors.append('Unexpected reports were produced:\n  {}'.format(
                        '\n  '.join(sorted(extra_report_ids))))
                else:
                    warnings.warn('Unexpected reports were produced:\n  {}'.format(
                        '\n  '.join(sorted(extra_report_ids))), InvalidOuputsWarning)

        # check expected outputs created: plots
        if os.path.isfile(os.path.join(out_dir, get_config().PLOTS_PATH)):
            archive = biosimulators_utils.archive.io.ArchiveReader().run(os.path.join(out_dir, 'plots.zip'))
            plot_ids = set(file.archive_path for file in archive.files)
        else:
            plot_ids = set()

        expected_plot_ids = set(plot.id for plot in self.expected_plots)

        missing_plot_ids = expected_plot_ids.difference(plot_ids)
        extra_plot_ids = plot_ids.difference(expected_plot_ids)

        if missing_plot_ids:
            if self.assert_no_missing_plots:
                errors.append('Plots were not produced:\n  {}'.format(
                    '\n  '.join(sorted(missing_plot_ids))))
            else:
                warnings.warn('Plots were not produced:\n  {}'.format(
                    '\n  '.join(sorted(missing_plot_ids))), InvalidOuputsWarning)

        if extra_plot_ids:
            if self.assert_no_extra_plots:
                errors.append('Extra plots were not produced:\n  {}'.format(
                    '\n  '.join(sorted(extra_plot_ids))))
            else:
                warnings.warn('Extra plots were not produced:\n  {}'.format(
                    '\n  '.join(sorted(extra_plot_ids))), InvalidOuputsWarning)

        # cleanup outputs
        shutil.rmtree(out_dir)

        # raise errors
        if errors:
            raise InvalidOuputsException('\n\n'.join(errors))


class SyntheticCombineArchiveTestCase(AbstractTestCase):
    """ Test that involves a computationally-generated COMBINE/OMEX archive

    Attributes:
        id (:obj:`str`): id
        name (:obj:`str`): name
        description (:obj:`str`): description
        curated_combine_archive_test_cases (:obj:`list` of :obj:`CuratedCombineArchiveTestCase`):
            curated COMBINE/OMEX archives that can be used to generate example archives for testing
    """

    def __init__(self, id=None, name=None, description=None, curated_combine_archive_test_cases=None):
        """
        Args:
            id (:obj:`str`, optional): id
            name (:obj:`str`, optional): name
            description (:obj:`str`): description
            curated_combine_archive_test_cases (:obj:`list` of :obj:`CuratedCombineArchiveTestCase`, optional):
                curated COMBINE/OMEX archives that can be used to generate example archives for testing
        """
        self.id = id
        self.name = name
        self.description = description
        self.curated_combine_archive_test_cases = curated_combine_archive_test_cases or []

    def eval(self, specifications):
        """ Evaluate a simulator's performance on a test case

        Args:
            specifications (:obj:`dict`): specifications of the simulator to validate

        Raises:
            :obj:`Exception`: if the simulator did not pass the test case
        """
        temp_dir = tempfile.mkdtemp()

        # read curated archives and find one that is suitable for testing
        suitable_curated_archive = False
        for curated_combine_archive_test_case in self.curated_combine_archive_test_cases:
            # read archive
            curated_archive_filename = curated_combine_archive_test_case.filename
            shared_archive_dir = os.path.join(temp_dir, 'archive')

            curated_archive = CombineArchiveReader().run(curated_archive_filename, shared_archive_dir)
            curated_sed_docs = {}
            sedml_reader = SedmlSimulationReader()
            for content in curated_archive.contents:
                if re.match(CombineArchiveContentFormatPattern.SED_ML, content.format):
                    sed_doc = sedml_reader.run(os.path.join(shared_archive_dir, content.location))
                    curated_sed_docs[content.location] = sed_doc

            # see if archive is suitable for testing
            if self.is_curated_archive_suitable_for_building_synthetic_archive(curated_archive, curated_sed_docs):
                suitable_curated_archive = True
                break

            # cleanup
            shutil.rmtree(shared_archive_dir)

        if not suitable_curated_archive:
            warnings.warn('No curated COMBINE/OMEX archives are available to generate archives for testing',
                          IgnoredTestCaseWarning)
            shutil.rmtree(temp_dir)
            return

        synthetic_archive_filename = os.path.join(temp_dir, 'archive.omex')
        synthetic_archive, synthetic_sed_docs = self.build_synthetic_archive(curated_archive, curated_sed_docs)
        sedml_writer = SedmlSimulationWriter()
        for location, sed_doc in synthetic_sed_docs.items():
            sedml_writer.run(sed_doc, os.path.join(shared_archive_dir, location))
        CombineArchiveWriter().run(synthetic_archive, shared_archive_dir, synthetic_archive_filename)

        # use synthetic archive to test simulator
        outputs_dir = os.path.join(temp_dir, 'outputs')
        try:
            biosimulators_utils.simulator.exec.exec_sedml_docs_in_archive_with_containerized_simulator(
                synthetic_archive_filename, outputs_dir, specifications['image']['url'], pull_docker_image=True)

            self.eval_outputs(specifications, synthetic_archive, outputs_dir)
        finally:
            shutil.rmtree(temp_dir)

    @abc.abstractmethod
    def is_curated_archive_suitable_for_building_synthetic_archive(self, archive, sed_docs):
        """ Determine if a curated archive is suitable for generating a synthetic archive for testing

        Args:
            archive (:obj:`CombineArchive`): curated COMBINE/OMEX archive
            sed_docs (:obj:`dict` of :obj:`str` to :obj:`SedDocument`): map from locations to
                SED documents in curated archive

        Returns:
            :obj:`bool`: :obj:`True`, if the curated archive is suitable for generating a synthetic
                archive for testing
        """
        pass  # pragma: no cover

    @abc.abstractmethod
    def build_synthetic_archive(self, curated_archive, curated_sed_docs):
        """ Generate a synthetic archive for testing

        Args:
            curated_archive (:obj:`CombineArchive`): curated COMBINE/OMEX archive
            curated_sed_docs (:obj:`dict` of :obj:`str` to :obj:`SedDocument`): map from locations to
                SED documents in curated archive

        Returns:
            :obj:`tuple`:

                * :obj:`CombineArchive`: synthetic COMBINE/OMEX archive for testing the simulator
                * :obj:`dict` of :obj:`str` to :obj:`SedDocument`: map from locations to
                  SED documents in synthetic archive
        """
        pass  # pragma: no cover

    def eval_outputs(self, specifications, synthetic_archive, outputs_dir):
        """ Test that the expected outputs were created for the synthetic archive

        Args:
            specifications (:obj:`dict`): specifications of the simulator to validate
            synthetic_archive (:obj:`CombineArchive`): synthetic COMBINE/OMEX archive for testing the simulator
            outputs_dir (:obj:`str`): directory that contains the outputs produced from the execution of the synthetic archive
        """
        pass  # pragma: no cover


def find_cases(dir_name=None, ids=None):
    """ Collect test cases from a directory

    Args:
        dir_name (:obj:`str`, optional): path to find example COMBINE/OMEX archives
        id (:obj:`list` of :obj:`str`, optional): List of ids of test cases to verify. If :obj:`ids`
            is none, all test cases are verified.

    Returns:
        :obj:`list` of :obj:`CuratedCombineArchiveTestCase`: test cases
    """
    if dir_name is None:
        dir_name = EXAMPLES_DIR
    if not os.path.isdir(dir_name):
        warnings.warn('Directory of example COMBINE/OMEX archives is not available', IgnoredTestCaseWarning)

    cases = []
    ignored_ids = set()
    for md_filename in glob.glob(os.path.join(dir_name, '**/*.json'), recursive=True):
        rel_filename = os.path.relpath(md_filename, dir_name)
        id = os.path.splitext(rel_filename)[0]
        if ids is None or id in ids:
            case = CuratedCombineArchiveTestCase().from_json(dir_name, rel_filename)
            cases.append(case)
        else:
            ignored_ids.add(id)

    if ignored_ids:
        warnings.warn('Some test case(s) were ignored:\n  {}'.format('\n  '.join(sorted(ignored_ids))), IgnoredTestCaseWarning)

    # return cases
    return cases