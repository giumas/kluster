import numpy as np
import xarray as xr
import laspy
import os
from time import perf_counter
from datetime import datetime

from HSTB.kluster.pydro_helpers import is_pydro
from HSTB.kluster.pdal_entwine import build_entwine_points
from HSTB.kluster.xarray_helpers import distrib_zarr_write


class FqprExport:
    """
    Visualizations in Matplotlib built on top of FQPR class.  Includes animations of beam vectors and vessel
    orientation.

    Processed fqpr_generation.Fqpr instance is passed in as argument
    """

    def __init__(self, fqpr):
        """

        Parameters
        ----------
        fqpr
            Fqpr instance to export from
        """
        self.fqpr = fqpr

    def _generate_chunks_xyzdat(self, variable_name: str):
        """
        Merge the desired vars across systems to reform pings, and build the data for the distributed system to process.
        Export_xyzdat requires a full dataset flattened to a 'soundings' dimension but retaining the sectorwise
        indexing.

        Parameters
        ----------
        variable_name
            variable identifier for the array to write.  ex: 'z' or 'tvu'

        Returns
        -------
        list
            each element is a future object pointing to a dataset to write out in memory
        list
            chunk indices for each chunk
        dict
            chunk sizes for the write, zarr wants explicit chunksizes for each array that cannot change after array
            creation.  chunk sizes can be greater than data size.
        """

        if variable_name not in self.fqpr.multibeam.raw_ping[0]:
            self.fqpr.logger.warning('Skipping variable "{}", not found in dataset.'.format(variable_name))
            return None, None, None
        self.fqpr.logger.info('Constructing dataset for variable "{}"'.format(variable_name))

        # use the raw_ping chunksize to chunk the reformed pings.
        data_for_workers = []
        dset = self.fqpr.subset_variables([variable_name, 'frequency'])

        # flatten to get the 1d sounding data
        dset_stacked = dset.stack({'sounding': ('time', 'beam')})
        # now remove all nan values
        valid_idx = ~np.isnan(dset_stacked[variable_name])
        dset_stacked = dset_stacked.where(valid_idx, drop=True)
        finallength = dset_stacked.sounding.shape[0]

        # 1000000 soundings gets you about 1MB chunks, which is what zarr recommends
        chnksize = np.min([finallength, 1000000])
        chnks = [[i * chnksize, i * chnksize + chnksize] for i in range(int(finallength / chnksize))]
        chnks[-1][1] = finallength
        chnksize_dict = {'beam_number': (1000000,), 'system_identifier': (1000000,), 'time': (1000000,), 'frequency': (1000000,),
                         'thu': (1000000,), 'tvu': (1000000,), 'x': (1000000,), 'y': (1000000,), 'z': (1000000,)}

        for c in chnks:
            vrs = {variable_name: (['time'], dset_stacked[variable_name].values[c[0]:c[1]]),
                   'system_identifier': (['time'], dset_stacked.system_identifier[c[0]:c[1]]),
                   'frequency': (['time'], dset_stacked.frequency[c[0]:c[1]])}
            coords = {'beam_number': (['time'], dset_stacked.beam.values[c[0]:c[1]]),
                      'time': dset_stacked.time.values[c[0]:c[1]]}
            ds = xr.Dataset(vrs, coords)
            data_for_workers.append(self.fqpr.client.scatter(ds))

        return data_for_workers, chnks, chnksize_dict

    def _generate_export_data(self, ping_dataset: xr.Dataset, filter_by_detection: bool = True, z_pos_down: bool = True):
        """
        Take the georeferenced data in the multibeam.raw_ping datasets held by fqpr_generation.Fqpr (ping_dataset is one of those
        raw_ping datasets) and build the necessary arrays for exporting.

        Parameters
        ----------
        ping_dataset
            one of the multibeam.raw_ping xarray Datasets, must contain the x,y,z variables generated by georeferencing
        filter_by_detection
            if True, will filter the xyz data by the detection info flag (rejected by multibeam system)
        z_pos_down
            if True, will export soundings with z positive down (this is the native Kluster convention)

        Returns
        -------
        xr.DataArray
            x variable stacked in the time/beam dimension to create 1 dim representation.  rejected soundings removed
            if filter_by_detection
        xr.DataArray
            y variable stacked in the time/beam dimension to create 1 dim representation.  rejected soundings removed
            if filter_by_detection
        xr.DataArray
            z variable stacked in the time/beam dimension to create 1 dim representation.  rejected soundings removed
            if filter_by_detection
        xr.DataArray
            uncertainty variable stacked in the time/beam dimension to create 1 dim representation.  rejected soundings removed
            if filter_by_detection
        np.array
            indexes of the original z data before stacking, used to unstack x
        np.array
            if detectioninfo exists, this is the integer classification for each sounding
        np.array
            if detectioninfo exists, boolean mask for the valid detections
        bool
            if tvu exists, True
        """

        uncertainty_included = False
        nan_mask = ~np.isnan(ping_dataset['x'])
        x_stck = ping_dataset['x'][nan_mask]
        y_stck = ping_dataset['y'][nan_mask]
        z_stck = ping_dataset['z'][nan_mask]
        if 'tvu' in ping_dataset:
            uncertainty_included = True
            unc_stck = ping_dataset['tvu'][nan_mask]

        # build mask with kongsberg detection info
        classification = None
        valid_detections = None
        if 'detectioninfo' in ping_dataset:
            dinfo = ping_dataset.detectioninfo
            filter_stck = dinfo.values[nan_mask]
            # filter_idx, filter_stck = stack_nan_array(dinfo, stack_dims=('time', 'beam'))
            valid_detections = filter_stck != 2
            tot = len(filter_stck)
            tot_valid = np.count_nonzero(valid_detections)
            tot_invalid = tot - tot_valid
            self.fqpr.logger.info(
                '{}: {} total soundings, {} retained, {} filtered'.format(ping_dataset.system_identifier, tot, tot_valid,
                                                                          tot_invalid))
        # filter points by mask
        unc = None
        if filter_by_detection and valid_detections is not None:
            x = x_stck[valid_detections]
            y = y_stck[valid_detections]
            z = z_stck[valid_detections]
            classification = filter_stck[valid_detections]
            if uncertainty_included:
                unc = unc_stck[valid_detections]
        else:
            x = x_stck
            y = y_stck
            z = z_stck
            if 'detectioninfo' in ping_dataset:
                classification = filter_stck
            if uncertainty_included:
                unc = unc_stck

        # z positive down is the native convention in Kluster, if you want positive up, gotta flip
        if not z_pos_down:
            z = z * -1

        return x, y, z, unc, nan_mask, classification, valid_detections, uncertainty_included

    def export_pings_to_file(self, output_directory: str = None, file_format: str = 'csv', csv_delimiter=' ',
                             filter_by_detection: bool = True, z_pos_down: bool = True, export_by_identifiers: bool = True):
        """
        Uses the output of georef_along_across_depth to build sounding exports.  Currently you can export to csv, las or
        entwine file formats, see file_format argument.

        If you export to las and want to retain rejected soundings under the noise classification, set
        filter_by_detection to False.

        Filters using the detectioninfo variable if present in multibeam and filter_by_detection is set.  Set z_pos_down
        to False if you want positive up.  Otherwise you get positive down.

        Will generate an xyz file for each sector in multibeam.  Results in one xyz file for each freq/sector id/serial
        number combination.

        entwine export will build las first, and then entwine from las

        Parameters
        ----------
        output_directory
            optional, destination directory for the xyz exports, otherwise will auto export next to converted data
        file_format
            optional, destination file format, default is csv file, options include ['csv', 'las', 'entwine']
        csv_delimiter
            optional, if you choose file_format=csv, this will control the delimiter
        filter_by_detection
            optional, if True will only write soundings that are not rejected
        z_pos_down
            if True, will export soundings with z positive down (this is the native Kluster convention)
        export_by_identifiers
            if True, will generate separate files for each combination of serial number/sector/frequency

        Returns
        -------
        list
            list of written file paths
        """

        if 'x' not in self.fqpr.multibeam.raw_ping[0]:
            self.fqpr.logger.error('export_pings_to_file: No xyz data found, please run All Processing - Georeference Soundings first.')
            return
        if file_format not in ['csv', 'las', 'entwine']:
            self.fqpr.logger.error('export_pings_to_file: Only csv, las and entwine format options supported at this time')
            return
        if file_format == 'entwine' and not is_pydro():
            self.fqpr.logger.error(
             'export_pings_to_file: Only pydro environments support entwine tile building.  Please see https://entwine.io/configuration.html for instructions on installing entwine if you wish to use entwine outside of Kluster.  Kluster exported las files will work with the entwine build command')

        if output_directory is None:
            output_directory = self.fqpr.multibeam.converted_pth

        self.fqpr.logger.info('****Exporting xyz data to {}****'.format(file_format))

        if file_format == 'csv':
            fldr_path = _create_folder(output_directory, 'csv_export')
            written_files = self._export_pings_to_csv(output_directory=fldr_path, csv_delimiter=csv_delimiter,
                                                      filter_by_detection=filter_by_detection, z_pos_down=z_pos_down,
                                                      export_by_identifiers=export_by_identifiers)
        elif file_format == 'las':
            fldr_path = _create_folder(output_directory, 'las_export')
            written_files = self._export_pings_to_las(output_directory=fldr_path, filter_by_detection=filter_by_detection,
                                                      z_pos_down=z_pos_down, export_by_identifiers=export_by_identifiers)
        elif file_format == 'entwine':
            fldr_path = _create_folder(output_directory, 'las_export')
            entwine_fldr_path = _create_folder(output_directory, 'entwine_export')
            written_files = self.export_pings_to_entwine(output_directory=entwine_fldr_path, las_export_folder=fldr_path,
                                                         filter_by_detection=filter_by_detection, z_pos_down=z_pos_down,
                                                         export_by_identifiers=export_by_identifiers)
        return written_files

    def _export_pings_to_csv(self, output_directory: str = None, csv_delimiter=' ', filter_by_detection: bool = True,
                             z_pos_down: bool = True, export_by_identifiers: bool = True):
        """
        Method for exporting pings to csv files.  See export_pings_to_file to use.

        Parameters
        ----------
        output_directory
            destination directory for the xyz exports, otherwise will auto export next to converted data
        csv_delimiter
            optional, if you choose file_format=csv, this will control the delimiter
        filter_by_detection
            optional, if True will only write soundings that are not rejected
        z_pos_down
            if True, will export soundings with z positive down (this is the native Kluster convention)
        export_by_identifiers
            if True, will generate separate files for each combination of serial number/sector/frequency

        Returns
        -------
        list
            list of written file paths
        """

        starttime = perf_counter()
        written_files = []

        for rp in self.fqpr.multibeam.raw_ping:
            self.fqpr.logger.info('Operating on system {}'.format(rp.system_identifier))
            if filter_by_detection and 'detectioninfo' not in rp:
                self.fqpr.logger.error('_export_pings_to_csv: Unable to filter by detection type, detectioninfo not found')
                return
            rp = rp.stack({'sounding': ('time', 'beam')})
            if export_by_identifiers:
                for freq in np.unique(rp.frequency):
                    subset_rp = rp.where(rp.frequency == freq, drop=True)
                    for secid in np.unique(subset_rp.txsector_beam).astype(np.int):
                        sec_subset_rp = subset_rp.where(subset_rp.txsector_beam == secid, drop=True)
                        dest_path = os.path.join(output_directory, '{}_{}_{}.csv'.format(rp.system_identifier, secid, freq))
                        self.fqpr.logger.info('writing to {}'.format(dest_path))
                        export_data = self._generate_export_data(sec_subset_rp, filter_by_detection=filter_by_detection, z_pos_down=z_pos_down)
                        self._csv_write(export_data[0], export_data[1], export_data[2], export_data[3], export_data[7],
                                        dest_path, csv_delimiter)
                        written_files.append(dest_path)

            else:
                dest_path = os.path.join(output_directory, rp.system_identifier + '.csv')
                self.fqpr.logger.info('writing to {}'.format(dest_path))
                export_data = self._generate_export_data(rp, filter_by_detection=filter_by_detection, z_pos_down=z_pos_down)
                self._csv_write(export_data[0], export_data[1], export_data[2], export_data[3], export_data[7],
                                dest_path, csv_delimiter)
                written_files.append(dest_path)

        endtime = perf_counter()
        self.fqpr.logger.info('****Exporting xyz data to csv complete: {}s****\n'.format(round(endtime - starttime, 1)))
        return written_files

    def _csv_write(self, x: xr.DataArray, y: xr.DataArray, z: xr.DataArray, uncertainty: xr.DataArray,
                   uncertainty_included: bool, dest_path: str, delimiter: str):
        """
        Write the data to csv

        Parameters
        ----------
        x
            x variable stacked in the time/beam dimension to create 1 dim representation.  rejected soundings removed
            if filter_by_detection
        y
            y variable stacked in the time/beam dimension to create 1 dim representation.  rejected soundings removed
            if filter_by_detection
        z
            z variable stacked in the time/beam dimension to create 1 dim representation.  rejected soundings removed
            if filter_by_detection
        uncertainty
            uncertainty variable stacked in the time/beam dimension to create 1 dim representation.  rejected soundings removed
            if filter_by_detection
        uncertainty_included
            if tvu exists, True
        dest_path
            output path to write to
        delimiter
            csv delimiter to use
        """

        if uncertainty_included:
            np.savetxt(dest_path, np.c_[x, y, z, uncertainty],
                       fmt=['%3.3f', '%2.3f', '%4.3f', '%4.3f'],
                       delimiter=delimiter,
                       header='easting{}northing{}depth{}uncertainty'.format(delimiter, delimiter, delimiter),
                       comments='')
        else:
            np.savetxt(dest_path, np.c_[x, y, z],
                       fmt=['%3.3f', '%2.3f', '%4.3f'],
                       delimiter=delimiter,
                       header='easting{}northing{}depth'.format(delimiter, delimiter),
                       comments='')

    def _export_pings_to_las(self, output_directory: str = None, filter_by_detection: bool = True, z_pos_down: bool = True,
                             export_by_identifiers: bool = True):
        """
        Uses the output of georef_along_across_depth to build sounding exports.  Currently you can export to csv or las
        file formats, see file_format argument.

        If you export to las and want to retain rejected soundings under the noise classification, set
        filter_by_detection to False.

        Filters using the detectioninfo variable if present in multibeam and filter_by_detection is set.

        Will generate an xyz file for each sector in multibeam.  Results in one xyz file for each freq/sector id/serial
        number combination.

        entwine export will build las first, and then entwine from las

        Parameters
        ----------
        output_directory
            destination directory for the xyz exports, otherwise will auto export next to converted data
        filter_by_detection
            optional, if True will only write soundings that are not rejected
        z_pos_down
            if True, will export soundings with z positive down (this is the native Kluster convention)
        export_by_identifiers
            if True, will generate separate files for each combination of serial number/sector/frequency

        Returns
        -------
        list
            list of written file paths
        """

        starttime = perf_counter()
        written_files = []

        for rp in self.fqpr.multibeam.raw_ping:
            self.fqpr.logger.info('Operating on system {}'.format(rp.system_identifier))
            if filter_by_detection and 'detectioninfo' not in rp:
                self.fqpr.logger.error('_export_pings_to_las: Unable to filter by detection type, detectioninfo not found')
                return
            rp = rp.stack({'sounding': ('time', 'beam')})
            if export_by_identifiers:
                for freq in np.unique(rp.frequency):
                    subset_rp = rp.where(rp.frequency == freq, drop=True)
                    for secid in np.unique(subset_rp.txsector_beam).astype(np.int):
                        sec_subset_rp = subset_rp.where(subset_rp.txsector_beam == secid, drop=True)
                        dest_path = os.path.join(output_directory, '{}_{}_{}.las'.format(rp.system_identifier, secid, freq))
                        self.fqpr.logger.info('writing to {}'.format(dest_path))
                        export_data = self._generate_export_data(sec_subset_rp, filter_by_detection=filter_by_detection, z_pos_down=z_pos_down)
                        self._las_write(export_data[0], export_data[1], export_data[2], export_data[3],
                                        export_data[5], export_data[7], dest_path)
                        written_files.append(dest_path)
            else:
                dest_path = os.path.join(output_directory, rp.system_identifier + '.las')
                self.fqpr.logger.info('writing to {}'.format(dest_path))
                export_data = self._generate_export_data(rp, filter_by_detection=filter_by_detection, z_pos_down=z_pos_down)
                self._las_write(export_data[0], export_data[1], export_data[2], export_data[3],
                                export_data[5], export_data[7], dest_path)
                written_files.append(dest_path)

        endtime = perf_counter()
        self.fqpr.logger.info('****Exporting xyz data to las complete: {}s****\n'.format(round(endtime - starttime, 1)))
        return written_files

    def _las_write(self, x: xr.DataArray, y: xr.DataArray, z: xr.DataArray, uncertainty: xr.DataArray,
                   classification: np.array, uncertainty_included: bool, dest_path: str):
        """
        Write the data to LAS format

        Parameters
        ----------
        x
            x variable stacked in the time/beam dimension to create 1 dim representation.  rejected soundings removed
            if filter_by_detection
        y
            y variable stacked in the time/beam dimension to create 1 dim representation.  rejected soundings removed
            if filter_by_detection
        z
            z variable stacked in the time/beam dimension to create 1 dim representation.  rejected soundings removed
            if filter_by_detection
        uncertainty
            uncertainty variable stacked in the time/beam dimension to create 1 dim representation.  rejected soundings removed
            if filter_by_detection
        classification
            if detectioninfo exists, this is the integer classification for each sounding
        uncertainty_included
            if tvu exists, True
        dest_path
            output path to write to
        """

        x = np.round(x.values, 2)
        y = np.round(y.values, 2)
        z = np.round(z.values, 3)

        hdr = laspy.header.Header(file_version=1.4, point_format=3)  # pt format 3 includes GPS time
        hdr.x_scale = 0.01  # xyz precision, las stores data as int
        hdr.y_scale = 0.01
        hdr.z_scale = 0.001
        # offset apparently used to store only differences, but you still write the actual value?  needs more understanding.
        hdr.x_offset = np.floor(float(x.min()))
        hdr.y_offset = np.floor(float(y.min()))
        hdr.z_offset = np.floor(float(z.min()))
        outfile = laspy.file.File(dest_path, mode='w', header=hdr)
        outfile.x = x
        outfile.y = y
        outfile.z = z
        if classification is not None:
            classification[np.where(classification < 2)] = 1  # 1 = Unclassified according to LAS spec
            classification[np.where(classification == 2)] = 7  # 7 = Low Point (noise) according to LAS spec
            outfile.classification = classification.astype(np.int8)
        # if uncertainty_included:  # putting it in Intensity for now as integer mm, Intensity is an int16 field
        #     outfile.intensity = (uncertainty.values * 1000).astype(np.int16)
        outfile.close()

    def export_pings_to_entwine(self, output_directory: str = None, las_export_folder: str = None, filter_by_detection: bool = True,
                                z_pos_down: bool = True, export_by_identifiers: bool = True):
        """
        Uses the output of georef_along_across_depth to build sounding exports.  Currently you can export to csv or las
        file formats, see file_format argument.

        If you export to las and want to retain rejected soundings under the noise classification, set
        filter_by_detection to False.

        Filters using the detectioninfo variable if present in multibeam and filter_by_detection is set.

        Will generate an xyz file for each sector in multibeam.  Results in one xyz file for each freq/sector id/serial
        number combination.

        entwine export will build las first, and then entwine from las

        Parameters
        ----------
        output_directory
            destination directory for the entwine point tiles
        las_export_folder
            Folder to export the las files to
        filter_by_detection
            optional, if True will only write soundings that are not rejected
        z_pos_down
            if True, will export soundings with z positive down (this is the native Kluster convention)
        export_by_identifiers
            if True, will generate separate files for each combination of serial number/sector/frequency

        Returns
        -------
        list
            one element long list containing the entwine directory path
        """

        self._export_pings_to_las(output_directory=las_export_folder, filter_by_detection=filter_by_detection,
                                  z_pos_down=z_pos_down, export_by_identifiers=export_by_identifiers)

        build_entwine_points(las_export_folder, output_directory)
        return [las_export_folder]

    def export_pings_to_dataset(self, outfold: str = None, validate: bool = False):
        """
        Write out data variable by variable to the final sounding data store.  Requires existence of georeferenced
        soundings to perform this function.

        Use subset_variables to build the arrays before exporting.  Only necessary for the dual head case, where
        there are two separate zarr stores for each head.

        Parameters
        ----------
        outfold
            destination directory for the xyz exports
        validate
            if True will use assert statement to verify that the number of soundings between pre and post exported
            data is equal
        """

        self.fqpr.logger.info('\n****Exporting xyz data to dataset****')
        starttime = perf_counter()

        if 'x' not in self.fqpr.multibeam.raw_ping[0]:
            self.fqpr.logger.error('No xyz data found')
            return

        if outfold is None:
            outfold = os.path.join(self.fqpr.multibeam.converted_pth, 'soundings.zarr')
        if os.path.exists(outfold):
            self.fqpr.logger.error(
                'export_pings_to_dataset: dataset exists already ({}), please remove and run'.format(outfold))
            raise NotImplementedError(
                'export_pings_to_dataset: dataset exists already ({}), please remove and run'.format(outfold))

        vars_of_interest = ('x', 'y', 'z', 'tvu', 'thu')

        # build the attributes we want in the final array.  Everything from the raw_ping plus what we need to make
        #    sense of our new indexes seems good.
        exist_attrs = self.fqpr.multibeam.raw_ping[0].attrs.copy()
        exist_attrs['xyzdat_export_time'] = datetime.utcnow().strftime('%c')

        for cnt, v in enumerate(vars_of_interest):
            merge = False
            if cnt != 0:
                # after the first write where we create the dataset, we need to flag subsequent writes as merge
                merge = True

            data_for_workers, write_chnk_idxs, chunk_sizes = self._generate_chunks_xyzdat(v)
            if data_for_workers is not None:
                final_size = write_chnk_idxs[-1][-1]
                fpths = distrib_zarr_write(outfold, data_for_workers, exist_attrs, chunk_sizes, write_chnk_idxs,
                                           final_size, self.fqpr.client, append_dim='time',
                                           show_progress=self.fqpr.show_progress)
        self.fqpr.soundings_path = outfold
        self.fqpr.reload_soundings_records()

        if validate:
            # ensure the sounding count matches
            pre_soundings_count = np.sum([np.count_nonzero(~np.isnan(f.x)) for f in self.fqpr.multibeam.raw_ping])
            post_soundings_count = self.fqpr.soundings.time.shape[0]
            assert pre_soundings_count == post_soundings_count
            self.fqpr.logger.info('export_pings_to_dataset validated successfully')

        endtime = perf_counter()
        self.fqpr.logger.info('****Exporting xyz data to dataset complete: {}s****\n'.format(round(endtime - starttime, 1)))


def _create_folder(output_directory, fldrname):
    tstmp = datetime.now().strftime('%Y%m%d_%H%M%S')
    try:
        fldr_path = os.path.join(output_directory, fldrname)
        os.mkdir(fldr_path)
    except FileExistsError:
        fldr_path = os.path.join(output_directory, fldrname + '_{}'.format(tstmp))
        os.mkdir(fldr_path)
    return fldr_path
