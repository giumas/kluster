import matplotlib
matplotlib.use('qt5agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib import cm
from matplotlib.figure import Figure

import numpy as np

from HSTB.kluster.gui.backends._qt import QtGui, QtCore, QtWidgets, Signal, qgis_enabled, backend
if qgis_enabled:
    from HSTB.kluster.gui.backends._qt import qgis_core, qgis_gui
from HSTB.kluster import kluster_variables

from vispy import use, visuals, scene
from vispy.util import keys
use(backend, 'gl2')


class ScaleAxisWidget(scene.AxisWidget):
    def __init__(self, add_factor: float = 0.0, **kwargs):
        super().__init__(**kwargs)
        self.unfreeze()
        self.add_factor = add_factor

    def _view_changed(self, event=None):
        """Linked view transform has changed; update ticks.
        """
        tr = self.node_transform(self._linked_view.scene)
        p1, p2 = tr.map(self._axis_ends())
        if self.orientation in ('left', 'right'):
            self.axis.domain = (p1[1] + self.add_factor, p2[1] + self.add_factor)
        else:
            self.axis.domain = (p1[0] + self.add_factor, p2[0] + self.add_factor)


class ColorBar(FigureCanvasQTAgg):
    """
    Custom widget with QT backend showing just a colorbar.  We can tack this on to our 3dview 2dview widgets to show
    a colorbar.  Seems like using matplotlib is the way to go, I wasn't able to find any native qt widget for this.
    """
    def __init__(self, parent=None, width: int = 1.1, height: int = 4, dpi: int = 100):
        self.parent = parent
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        self.fig.gca().set_visible(False)
        super().__init__(self.fig)
        self.c_map_ax = self.fig.add_axes([0.05, 0.05, 0.45, 0.9])
        self.c_map_ax.get_xaxis().set_visible(False)
        self.c_map_ax.get_yaxis().set_visible(False)

    def setup_colorbar(self, cmap: matplotlib.colors.Colormap, minval: float, maxval: float, is_rejected: bool = False,
                       by_name: list = None, invert_y: bool = False):
        """
        Provide a color map and a min max value to build the colorbar

        Parameters
        ----------
        cmap
            provide a colormap to use for the color bar
        minval
            min value of the color bar
        maxval
            max value of the color bar
        is_rejected
            if is rejected, set the custom tick labels
        by_name
            if this is populated, we will show the colorbar with ticks equal to the length of the list and labels equal
            to the list
        invert_y
            invert the y axis
        """

        self.c_map_ax.get_xaxis().set_visible(True)
        self.c_map_ax.get_yaxis().set_visible(True)
        self.c_map_ax.clear()
        norm = matplotlib.colors.Normalize(vmin=minval, vmax=maxval)
        if is_rejected:
            self.fig.colorbar(cm.ScalarMappable(norm=norm, cmap=cmap), orientation='vertical', cax=self.c_map_ax,
                              ticks=[2, 1, 0])
            self.c_map_ax.set_yticklabels(['Reject', 'Phase', 'Amp'])
            self.c_map_ax.tick_params(labelsize=8)
        elif by_name:
            self.fig.colorbar(cm.ScalarMappable(norm=norm, cmap=cmap), orientation='vertical', cax=self.c_map_ax,
                              ticks=(np.arange(len(by_name)) + 0.5).tolist())
            self.c_map_ax.set_yticklabels(by_name)
            self.c_map_ax.tick_params(labelsize=6)
        else:
            self.fig.colorbar(cm.ScalarMappable(norm=norm, cmap=cmap), orientation='vertical', cax=self.c_map_ax)
            self.c_map_ax.tick_params(labelsize=8)
        if invert_y:
            self.c_map_ax.invert_yaxis()
        self.draw()


class DockableCanvas(scene.SceneCanvas):
    """
    Calls to on_draw when the widget is undocked and you are dragging it around fail.  We need to catch those failed
    attempts here and just let them go.  Assumes it fails because of undocking actions...
    """
    def __init__(self, keys, show, parent):
        super().__init__(keys=keys, show=show, parent=parent)

    def on_draw(self, event):
        try:
            super().on_draw(event)
        except:
            pass


class PanZoomInteractive(scene.PanZoomCamera):
    """
    A custom camera for the 2d scatter plots.  Allows for interaction (i.e. querying of points) using the
    handle_data_selected callback.
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.selected_callback = None
        self.fresh_camera = True

    def _bind_selecting_event(self, selfunc):
        """
        Emit the top left/bottom right 3d coordinates of the selection.  Parent widget will control the 3dview and
        highlight the points as well as populating the explorer widget with the values so you can see.
        """
        self.selected_callback = selfunc

    def _handle_mousewheel_zoom_event(self, event):
        """
        Simple mousewheel zoom event handler, alters the zoom attribute
        """
        center = self._scene_transform.imap(event.pos)
        self.zoom((1 + self.zoom_factor) ** (-event.delta[1] * 30), center)
        self.fresh_camera = False

    def _handle_zoom_event(self, event):
        """
        Set the scale and center according to the new zoom.  Triggered on moving the mouse with right click.
        """

        p1c = np.array(event.last_event.pos)[:2]
        p2c = np.array(event.pos)[:2]
        scale = ((1 + self.zoom_factor) ** ((p1c - p2c) *
                                            np.array([1, -1])))
        center = self._transform.imap(event.press_event.pos[:2])
        self.zoom(scale, center)
        self.fresh_camera = False

    def _handle_translate_event(self, event):
        """
        Move the camera center according event pos, last pos.  This is called when dragging the mouse in translate
        mode.
        """
        p1 = np.array(event.last_event.pos)[:2]
        p2 = np.array(event.pos)[:2]
        p1s = self._transform.imap(p1)
        p2s = self._transform.imap(p2)
        self.pan(p1s - p2s)
        self.fresh_camera = False

    def _handle_data_selected(self, startpos, endpos):
        """
        Runs the parent method (selected_callback) to select points when the user holds down control and selects points
        with this camera.  Parent will highlight data and populate explorer widget with attributes

        Parameters
        ----------
        startpos
            click position in screen coordinates
        endpos
            mouse click release position in screen coordinates
        """

        if self.selected_callback:
            startpos = [startpos[0] - 80, startpos[1] - 10]  # camera transform seems to not handle the new twod_grid
            endpos = [endpos[0] - 80, endpos[1] - 10]        # add these on to handle the buffers until we figure it out
            startpos = self._transform.imap(startpos)
            endpos = self._transform.imap(endpos)
            new_startpos = np.array([min(startpos[0], endpos[0]), min(startpos[1], endpos[1])])
            new_endpos = np.array([max(startpos[0], endpos[0]), max(startpos[1], endpos[1])])

            if (new_startpos == new_endpos).all():
                new_startpos -= np.array([0.1, 0.02])
                new_endpos += np.array([0.1, 0.02])
            self.selected_callback(new_startpos, new_endpos, three_d=False)

    def viewbox_mouse_event(self, event):
        """
        The SubScene received a mouse event; update transform accordingly.

        Parameters
        ----------
        event : instance of Event
            The event.
        """
        if event.handled or not self.interactive:
            return

        if event.type == 'mouse_wheel':
            self._handle_mousewheel_zoom_event(event)
            event.handled = True
        elif event.type == 'mouse_release':
            if event.press_event is None:
                return

            modifiers = event.mouse_event.modifiers
            p1 = event.mouse_event.press_event.pos
            p2 = event.mouse_event.pos
            if 1 in event.buttons and keys.CONTROL in modifiers:
                self._handle_data_selected(p1, p2)
            event.handled = True
        elif event.type == 'mouse_move':
            if event.press_event is None:
                return

            modifiers = event.mouse_event.modifiers
            if 1 in event.buttons and not modifiers:
                self._handle_translate_event(event)
                event.handled = True
            elif 1 in event.buttons and keys.CONTROL in modifiers:
                event.handled = True
            elif 2 in event.buttons and not modifiers:
                self._handle_zoom_event(event)
                event.handled = True
            else:
                event.handled = False
        elif event.type == 'mouse_press':
            event.handled = True
        else:
            event.handled = False


class TurntableCameraInteractive(scene.TurntableCamera):
    """
    A custom camera for 3d scatter plots.  Allows for interaction (i.e. querying of points) using the handle_data_selected
    callback.  I haven't quite yet figured out the selecting 3d points using 2d pixel coordinates just yet, it's been
    a bit of a struggle.  Currently I try to just select all in the z range so that I only have to deal with x and y.
    This is somewhat successful.
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.selected_callback = None
        self.fresh_camera = True
        self.min_z = None
        self.max_z = None

    def set_z_limits(self, minz, maxz):
        self.min_z = minz
        self.max_z = maxz

    def _bind_selecting_event(self, selfunc):
        """
        Emit the top left/bottom right 3d coordinates of the selection.  Parent widget will control the 3dview and
        highlight the points as well as populating the explorer widget with the values so you can see.
        """
        self.selected_callback = selfunc

    def _3drot_vector(self, x, y, z, inv: bool = False):
        """
        Rotate the provided vector coordinates to camera roll, azimuth, elevation
        """
        # modeled after the _dist_to_trans method, appears to be some kind of almost YXZ tait-bryan standard.  I can't
        # seem to replicate this using scipy rotation
        rae = np.array([self.roll, self.azimuth, self.elevation]) * np.pi / 180
        sro, saz, sel = np.sin(rae)
        cro, caz, cel = np.cos(rae)
        if not inv:
            dx = (+ x * (cro * caz + sro * sel * saz)
                  + y * (sro * caz - cro * sel * saz)
                  + z * (cel * saz))
            dy = (+ x * (cro * saz - sro * sel * caz)
                  + y * (sro * saz + cro * sel * caz)
                  + z * (cel * caz))
            dz = (- x * (sro * cel)
                  + y * (cro * cel)
                  + z * sel)
        else:  # this rotates from already rotated data coordinates to pixel camera coordinates
            dx = (+ x * (cro * caz + sro * sel * saz)
                  + y * (cro * saz - sro * sel * caz)
                  - z * (sro * cel))
            dy = (+ x * (sro * caz - cro * sel * saz)
                  + y * (sro * saz + cro * sel * caz)
                  + z * (cro * cel))
            dz = (+ x * (cel * saz)
                  + y * (cel * caz)
                  + z * sel)
        return dx, dy, dz

    def _dist_between_mouse_coords(self, start_pos: np.array, end_pos: np.array):
        """
        Build the distance between the two screen coordinate arrays, taking into account the camera distance (i.e. zoom
        level).  Returns distance in data coordinates

        Parameters
        ----------
        start_pos
            (x position, y position) for start of distance vector
        end_pos
            (x position, y position) for end of distance vector

        Returns
        -------
        np.array
            (x distance, y distance)
        """

        dist = (start_pos - end_pos) / self._viewbox.size * self.distance
        return dist

    def _handle_translate_event(self, start_pos, end_pos):
        """
        Move the camera center according to the points provided.  This is called when dragging the mouse in translate
        mode.

        Parameters
        ----------
        start_pos
            Point where you first clicked
        end_pos
            Point where you released the mouse button after dragging
        """
        self.fresh_camera = False
        if self._event_value is None or len(self._event_value) == 2:
            self._event_value = self.center
        dist = self._dist_between_mouse_coords(start_pos, end_pos)
        dist[1] *= -1
        dx, dy, dz = self._3drot_vector(dist[0], dist[1], 0)
        # Black magic part 2: take up-vector and flipping into account
        ff = self._flip_factors
        up, forward, right = self._get_dim_vectors()
        dx, dy, dz = right * dx + forward * dy + up * dz
        dx, dy, dz = ff[0] * dx, ff[1] * dy, dz * ff[2]
        c = self._event_value
        self.center = c[0] + dx, c[1] + dy, c[2] + dz

    def _handle_zoom_event(self, distance):
        """
        Set the scale_factor and distance according to the new zoom.  Triggered on moving the mouse with right click.

        Parameters
        ----------
        distance
            distance the mouse moved since right click in 2d screen coordinates, i.e. (200,150)
        """

        # Zoom
        self.fresh_camera = False
        if self._event_value is None:
            self._event_value = (self._scale_factor, self._distance)
        zoomy = (1 + self.zoom_factor) ** distance[1]

        self.scale_factor = self._event_value[0] * zoomy
        # Modify distance if its given
        if self._distance is not None:
            self._distance = self._event_value[1] * zoomy
        self.view_changed()

    def _handle_mousewheel_zoom_event(self, event):
        """
        Simple mousewheel zoom event handler, alters the scale_factor and distance using the 1.1 constant
        """
        self.fresh_camera = False
        s = 1.1 ** - event.delta[1]
        self._scale_factor *= s
        if self._distance is not None:
            self._distance *= s
        self.view_changed()

    def screen_to_data_coordinate(self, x: float, y: float):
        """
        Take data coordinates provided here and convert to screen coordinates.  Will return a list of two points, one the
        data coordinate in the foreground, and one the data coordinate in the background.
        """
        pt = np.array([x, y])
        center_mouse_coords = np.array(self._viewbox.size) / 2

        dist_cnrn = self._dist_between_mouse_coords(center_mouse_coords, pt)
        dist_crnr_back = self._3drot_vector(-dist_cnrn[0], dist_cnrn[1], 0)
        dist_crnr_front = self._3drot_vector(-dist_cnrn[0], dist_cnrn[1], 1)
        if dist_crnr_front[2] > dist_crnr_back[2]:
            final_pts = [np.array(dist_crnr_back) + np.array(self.center), np.array(dist_crnr_front) + np.array(self.center)]
        else:
            final_pts = [np.array(dist_crnr_front) + np.array(self.center), np.array(dist_crnr_back) + np.array(self.center)]
        return final_pts

    def _screen_corners_data_coordinates(self):
        """
        Take the screen coordinates of the corner points of the view (in pixels) and return the corner coordinates
        in data coordinates.  EX: top left (0,0) might be converted to (-3, 1, 10) if the top left is that in data
        coordinates.

        Returns
        -------
        list
            list of lists, [top left back, top left forward, top right back, top right forward,
                            bottom left back, bottom left forward, bottom left back, bottom left forward]
        """

        final_corner_points = []
        corner_pts = [np.array([0, 0]), np.array([self._viewbox.size[0], 0]), np.array([0, self._viewbox.size[1]]),
                      np.array([self._viewbox.size[0], self._viewbox.size[1]])]
        for crnr in corner_pts:
            final_corners = self.screen_to_data_coordinate(crnr[0], crnr[1])
            final_corner_points.append(final_corners[0])
            final_corner_points.append(final_corners[1])
        return np.array(final_corner_points)

    def data_coordinates_to_screen(self, x, y, z):
        newx, newy, newz = self._3drot_vector(x, y, z, inv=True)
        print('rotated {}->{} {}->{} {}->{}'.format(round(newx.min(), 3), round(newx.max(), 3), round(newy.min(), 3),
                                                    round(newy.max(), 3), round(newz.min(), 3), round(newz.max(), 3)))
        print('factor = {}'.format((1 / self._distance / np.sqrt(2)) * self._viewbox.size[0]))
        newx = (newx) / (self._distance / np.sqrt(2)) * self._viewbox.size[0]
        newy = (newy) / (self._distance / np.sqrt(2)) * self._viewbox.size[1]
        return newx, newy

    def _handle_data_selected(self, startpos, endpos):
        """
        Runs the parent method (selected_callback) to select points when the user holds down control and selects points
        with this camera.  Parent will highlight data and populate explorer widget with attributes

        Parameters
        ----------
        startpos
            click position in screen coordinates
        endpos
            mouse click release position in screen coordinates
        """
        # TODO
        # build 4 infinite lines (each screen xy corner) and rotate (rotate equations of lines into utm)
        #  - assume 0 and 1 for z to get unit vector
        # solve for corners to get utm xyz for each end of segment, gives volume in utm
        #  - have to extend segment to min max utm z, should alter utm x y and z
        # clip point utm data to the min/max volume xy
        # inv rotate clipped xyz to screen xy to get subset of points to select from
        #  - think about check here to see if too many points selected
        # select from drag-selected xy to get selected points

        print('Selecting data in 3d is currently not implemented.')
        # if self.selected_callback:
        #     if (startpos == endpos).all():
        #         startpos -= 10
        #         endpos += 10
        #     new_startpos = np.array([int(min(startpos[0], endpos[0])), int(min(startpos[1], endpos[1]))])
        #     new_endpos = np.array([int(max(startpos[0], endpos[0])), int(max(startpos[1], endpos[1]))])
        #     new_startpos = self.screen_to_data_coordinate(new_startpos[0], new_startpos[1])
        #     new_endpos = self.screen_to_data_coordinate(new_endpos[0], new_endpos[1])
        #     self.selected_callback(new_startpos, new_endpos, three_d=True)

    def viewbox_mouse_event(self, event):
        """
        Handles all the mouse events allowed in this camera.  Includes:
        - mouse wheel zoom
        - move camera center with shift+left click and drag
        - select points with ctrl+left click and drag
        - rotate camera with left click and drag
        - zoom with right click and drag

        Parameters
        ----------
        event
            mouse event
        """

        if event.handled or not self.interactive:
            return

        if event.type == 'mouse_wheel':
            self._handle_mousewheel_zoom_event(event)
        elif event.type == 'mouse_release':
            if event.press_event is None:
                self._event_value = None  # Reset
                return

            modifiers = event.mouse_event.modifiers
            p1 = event.mouse_event.press_event.pos
            p2 = event.mouse_event.pos
            if 1 in event.buttons and keys.CONTROL in modifiers:
                self._handle_data_selected(p1, p2)
            self._event_value = None  # Reset
            event.handled = True
        elif event.type == 'mouse_press':
            event.handled = True
        elif event.type == 'mouse_move':
            if event.press_event is None:
                return

            modifiers = event.mouse_event.modifiers
            p1 = event.mouse_event.press_event.pos
            p2 = event.mouse_event.pos
            d = p2 - p1
            if 1 in event.buttons and not modifiers:
                self._update_rotation(event)
            elif 1 in event.buttons and keys.SHIFT in modifiers:
                self._handle_translate_event(p1, p2)
            elif 2 in event.buttons:
                self._handle_zoom_event(d)
        else:
            event.handled = False


class ThreeDView(QtWidgets.QWidget):
    """
    Widget containing the Vispy scene.  Controlled with mouse (rotation/translation) and dictated by the values
    shown in the OptionsWidget.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.canvas = DockableCanvas(keys='interactive', show=True, parent=parent)
        self.view = self.canvas.central_widget.add_view()
        self.axis_x = None
        self.axis_y = None
        self.axis_z = None
        self.twod_grid = None
        # self.axis_labels = None

        self.is_3d = True

        self.scatter = None
        self.scatter_transform = None
        self.scatter_select_range = None

        self.id = np.array([])
        self.head = np.array([])
        self.x = np.array([])
        self.y = np.array([])
        self.z = np.array([])
        self.rotx = np.array([])
        self.roty = np.array([])
        self.tvu = np.array([])
        self.rejected = np.array([])
        self.pointtime = np.array([])
        self.beam = np.array([])
        self.linename = np.array([])

        # statistics are populated on display_points
        self.x_offset = 0.0
        self.y_offset = 0.0
        self.z_offset = 0.0
        self.min_x = 0
        self.min_y = 0
        self.min_z = 0
        self.min_tvu = 0
        self.min_rejected = 0
        self.min_beam = 0
        self.max_x = 0
        self.max_y = 0
        self.max_z = 0
        self.max_tvu = 0
        self.max_rejected = 0
        self.max_beam = 0
        self.mean_x = 0
        self.mean_y = 0
        self.mean_z = 0
        self.mean_tvu = 0
        self.mean_rejected = 0
        self.mean_beam = 0
        self.unique_systems = []
        self.unique_linenames = []

        self.vertical_exaggeration = 1.0
        self.view_direction = 'north'
        self.show_axis = True

        self.displayed_points = None
        self.selected_points = None
        self.superselected_index = None

        layout = QtWidgets.QHBoxLayout()
        layout.addWidget(self.canvas.native)
        self.setLayout(layout)

    def _select_points(self, startpos, endpos, three_d: bool = False):
        """
        Trigger the parent method to highlight and display point data within the bounds provided by the two points

        Parameters
        ----------
        startpos
            Point where you first clicked
        endpos
            Point where you released the mouse button after dragging
        """

        if self.displayed_points is not None and self.parent is not None:
            self.parent.select_points(startpos, endpos, three_d=three_d)

    def add_points(self, head: np.array, x: np.array, y: np.array, z: np.array, tvu: np.array, rejected: np.array,
                   pointtime: np.array, beam: np.array, newid: str, linename: np.array, is_3d: bool, azimuth: float = None):
        """
        Add points to the 3d view widget, we only display points after all points are added, hence the separate methods

        Parameters
        ----------
        head
            head index of the sounding
        x
            easting
        y
            northing
        z
            depth value, positive down assumed
        tvu
            vertical uncertainty
        rejected
            The accepted/rejected state of each beam.  2 = rejected, 1 = phase detection, 0 = amplitude detection
        pointtime
            time of the sounding
        beam
            beam number of the sounding
        newid
            container name the sounding came from, ex: 'EM710_234_02_10_2019'
        linename
            1d array of line names for each time
        is_3d
            Set this flag to notify widget that we are in 3d mode
        azimuth
            azimuth of the selection polygon in radians
        """

        if azimuth:
            cos_az = np.cos(azimuth)
            sin_az = np.sin(azimuth)

            rotx = cos_az * x - sin_az * y
            roty = sin_az * x + cos_az * y
            self.rotx = np.concatenate([self.rotx, rotx])
            self.roty = np.concatenate([self.roty, roty])
        else:
            self.rotx = np.concatenate([self.rotx, x])
            self.roty = np.concatenate([self.roty, y])

        # expand the identifier to be the size of the input arrays
        self.id = np.concatenate([self.id, np.full(x.shape[0], newid)])
        self.head = np.concatenate([self.head, head])
        self.x = np.concatenate([self.x, x])
        self.y = np.concatenate([self.y, y])
        self.z = np.concatenate([self.z, z])
        self.tvu = np.concatenate([self.tvu, tvu])
        self.rejected = np.concatenate([self.rejected, rejected])
        self.pointtime = np.concatenate([self.pointtime, pointtime])
        self.beam = np.concatenate([self.beam, beam])
        self.linename = np.concatenate([self.linename, linename])
        self.is_3d = is_3d

    def return_points(self):
        return [self.id, self.head, self.x, self.y, self.z, self.tvu, self.rejected, self.pointtime, self.beam, self.linename]

    def _configure_2d_3d_view(self):
        """
        Due to differences in how the view is constructed when we switch back and forth between 2d and 3d views, we
        have to rebuild the view for each mode
        """

        if self.axis_x is not None:
            self.axis_x.parent = None
            self.axis_x = None
        if self.axis_y is not None:
            self.axis_y.parent = None
            self.axis_y = None
        if self.axis_z is not None:
            self.axis_z.parent = None
            self.axis_z = None
        if self.is_3d:
            if self.twod_grid:
                self.canvas.central_widget.remove_widget(self.twod_grid)
            self.twod_grid = None
            self.view = None
            self.view = self.canvas.central_widget.add_view()
            self.view.camera = TurntableCameraInteractive()
        else:
            self.view = None
            self.twod_grid = self.canvas.central_widget.add_grid(margin=10)
            self.twod_grid.spacing = 0
            self.view = self.twod_grid.add_view(row=1, col=1, border_color='white')
            self.view.camera = PanZoomInteractive()
        self.view.camera._bind_selecting_event(self._select_points)

    def setup_axes(self):
        """
        Build the axes to match the scatter data loaded.  I use the axiswidget for 2d view, doesn't seem to work
        for 3d view.  I really like the ticks though.  I need something more sophisticated for 3d view.
        """

        if self.axis_x is not None:
            self.axis_x.parent = None
            self.axis_x = None
        if self.axis_y is not None:
            self.axis_y.parent = None
            self.axis_y = None
        if self.axis_z is not None:
            self.axis_z.parent = None
            self.axis_z = None

        if self.show_axis:
            if self.is_3d:  # just using arrows for now, nothing that cool
                if self.twod_grid:
                    self.canvas.central_widget.remove_widget(self.twod_grid)
                self.twod_grid = None
                diff_x = self.max_x - self.min_x
                self.axis_x = scene.visuals.Arrow(pos=np.array([[0, 0], [diff_x, 0]]), color='r', parent=self.view.scene,
                                                  arrows=np.array([[diff_x/50, 0, diff_x, 0], [diff_x/50, 0, diff_x, 0]]),
                                                  arrow_size=8, arrow_color='r', arrow_type='triangle_60')
                diff_y = self.max_y - self.min_y
                self.axis_y = scene.visuals.Arrow(pos=np.array([[0, 0], [0, diff_y]]), color='g', parent=self.view.scene,
                                                  arrows=np.array([[0, diff_y / 50, 0, diff_y], [0, diff_y / 50, 0, diff_y]]),
                                                  arrow_size=8, arrow_color='g', arrow_type='triangle_60')
                diff_z = (self.max_z - self.min_z) * self.vertical_exaggeration
                self.axis_z = scene.visuals.Arrow(pos=np.array([[0, 0, 0], [0, 0, diff_z]]), color='b', parent=self.view.scene,
                                                  arrows=np.array([[0, 0, diff_z/50, 0, 0, diff_z], [0, 0, diff_z/50, 0, 0, diff_z]]),
                                                  arrow_size=8, arrow_color='b', arrow_type='triangle_60')
            else:
                title = scene.Label(" ", color='white')
                title.height_max = 10
                self.twod_grid.add_widget(title, row=0, col=0, col_span=2)
                # if self.view_direction in ['north', 'right']:
                #     self.axis_x = ScaleAxisWidget(add_factor=self.x_offset, orientation='bottom', axis_font_size=12, axis_label_margin=50, tick_label_margin=18)
                # else:
                #     self.axis_x = ScaleAxisWidget(add_factor=self.y_offset, orientation='bottom', axis_font_size=12, axis_label_margin=50, tick_label_margin=18)
                self.axis_x = scene.AxisWidget(orientation='bottom', axis_font_size=12, axis_label_margin=50, tick_label_margin=18)
                self.axis_x.height_max = 80
                self.twod_grid.add_widget(self.axis_x, row=2, col=1)
                self.axis_z = ScaleAxisWidget(add_factor=-self.min_z, orientation='left', axis_font_size=12, axis_label_margin=50, tick_label_margin=5)
                self.axis_z.width_max = 80
                self.twod_grid.add_widget(self.axis_z, row=1, col=0)
                right_padding = self.twod_grid.add_widget(row=1, col=2, row_span=1)
                right_padding.width_max = 50
                self.axis_x.link_view(self.view)
                self.axis_z.link_view(self.view)

    def _build_color_by_soundings(self, color_by: str = 'depth'):
        """
        Build out a RGBA value for each point based on the color_by argument.  We use the matplotlib colormap to
        return these values.  If you pick something like system or linename, we just return a mapped value for each
        unique entry.

        Parameters
        ----------
        color_by
            one of depth, vertical_uncertainty, beam, rejected, system, linename

        Returns
        -------
        np.ndarray
            (N,4) array, where N is the number of points and the values are the RGBA values for each point
        matplotlib.colors.ColorMap
            cmap object that we use later to build the color bar
        float
            minimum value to use for the color bar
        float
            maximum value to use for the color bar
        """

        # normalize the arrays and build the colors for each sounding
        if color_by == 'depth':
            min_val = self.min_z
            max_val = self.max_z
            clrs, cmap = normalized_arr_to_rgb_v2((self.z - self.min_z) / (self.max_z - self.min_z), reverse=True)
        elif color_by == 'vertical_uncertainty':
            min_val = self.min_tvu
            max_val = self.max_tvu
            clrs, cmap = normalized_arr_to_rgb_v2((self.tvu - self.min_tvu) / (self.max_tvu - self.min_tvu))
        elif color_by == 'beam':
            min_val = self.min_beam
            max_val = self.max_beam
            clrs, cmap = normalized_arr_to_rgb_v2(self.beam / self.max_beam, band_count=self.max_beam)
        elif color_by == 'rejected':
            min_val = 0
            max_val = 2
            clrs, cmap = normalized_arr_to_rgb_v2(self.rejected / 2, band_count=3, colormap='bwr')
        elif color_by in ['system', 'linename']:
            min_val = 0
            if color_by == 'system':
                vari = self.id
                uvari = self.unique_systems
            else:
                vari = self.linename
                uvari = self.unique_linenames
            sys_idx = np.zeros(vari.shape[0], dtype=np.int32)
            for cnt, us in enumerate(uvari):
                usidx = vari == us
                sys_idx[usidx] = cnt
            max_val = len(uvari)
            clrs, cmap = normalized_arr_to_rgb_v2((sys_idx / max_val), band_count=max_val)
        else:
            raise ValueError('Coloring by {} is not supported at this time'.format(color_by))

        if self.selected_points is not None and self.selected_points.any():
            msk = np.zeros(self.displayed_points.shape[0], dtype=bool)
            msk[self.selected_points] = True
            clrs[msk, :] = kluster_variables.selected_point_color
            if self.superselected_index is not None:
                msk[:] = False
                msk[self.selected_points[self.superselected_index]] = True
                clrs[msk, :] = kluster_variables.super_selected_point_color

        return clrs, cmap, min_val, max_val

    def _build_scatter(self, clrs: np.ndarray):
        """
        Populate the scatter plot with data.  3d view gets the xyz, 2d view gets either xz or yz depending on view
        direction.  We center the camera on the mean value, and if this is the first time the camera is used (fresh
        camera) we automatically set the zoom level.

        Parameters
        ----------
        clrs
            (N,4) array, where N is the number of points and the values are the RGBA values for each point
        """

        if self.is_3d:
            self.scatter.set_data(self.displayed_points, edge_color=clrs, face_color=clrs, symbol='o', size=3)
            if self.view.camera.fresh_camera:
                self.view.camera.center = (self.mean_x - self.x_offset, self.mean_y - self.y_offset, self.mean_z - self.min_z)
                self.view.camera.distance = (self.max_x - self.x_offset) * 2
                self.view.camera.fresh_camera = False
        else:
            if self.view_direction in ['north']:
                self.scatter.set_data(self.displayed_points[:, [0, 2]], edge_color=clrs, face_color=clrs, symbol='o', size=3)
                self.view.camera.center = (self.mean_x - self.x_offset, self.mean_z - self.min_z)
                if self.view.camera.fresh_camera:
                    self.view.camera.zoom((self.max_x - self.x_offset) + 10)  # try and fit the swath in view on load
                    self.view.camera.fresh_camera = False
            elif self.view_direction in ['east', 'arrow']:
                self.scatter.set_data(self.displayed_points[:, [1, 2]], edge_color=clrs, face_color=clrs, symbol='o', size=3)
                self.view.camera.center = (self.mean_y - self.y_offset, self.mean_z - self.min_z)
                if self.view.camera.fresh_camera:
                    self.view.camera.zoom((self.max_y - self.y_offset) + 10)  # try and fit the swath in view on load
                    self.view.camera.fresh_camera = False

    def _build_statistics(self):
        """
        Triggered on display_points.  After all the points are added (add_points called over and over for each new
        point source) we run display_points, which calls this method to build the statistics for each variable.

        These are used later for constructing colormaps and setting the camera.
        """

        if self.view_direction in ['north', 'east', 'top']:
            self.min_x = np.nanmin(self.x)
            self.min_y = np.nanmin(self.y)
            self.max_x = np.nanmax(self.x)
            self.max_y = np.nanmax(self.y)
            self.mean_x = np.nanmean(self.x)
            self.mean_y = np.nanmean(self.y)
        else:
            self.min_x = np.nanmin(self.rotx)
            self.min_y = np.nanmin(self.roty)
            self.max_x = np.nanmax(self.rotx)
            self.max_y = np.nanmax(self.roty)
            self.mean_x = np.nanmean(self.rotx)
            self.mean_y = np.nanmean(self.roty)

        self.min_z = np.nanmin(self.z)
        self.min_tvu = np.nanmin(self.tvu)
        self.min_rejected = np.nanmin(self.rejected)
        self.min_beam = np.nanmin(self.beam)

        self.max_z = np.nanmax(self.z)
        self.max_tvu = np.nanmax(self.tvu)
        self.max_rejected = np.nanmax(self.rejected)
        self.max_beam = np.nanmax(self.beam)

        self.mean_z = np.nanmean(self.z)
        self.mean_tvu = np.nanmean(self.tvu)
        self.mean_rejected = np.nanmean(self.rejected)
        self.mean_beam = np.nanmean(self.beam)

        self.unique_systems = np.unique(self.id).tolist()
        self.unique_linenames = np.unique(self.linename).tolist()

        if self.is_3d:
            self.view.camera.set_z_limits(self.min_z, self.max_z)

    def display_points(self, color_by: str = 'depth', vertical_exaggeration: float = 1.0, view_direction: str = 'north',
                       show_axis: bool = True):
        """
        After adding all the points you want to add, call this method to then load them in opengl and draw them to the
        scatter plot

        Parameters
        ----------
        color_by
            identifer for the variable you want to color by.  One of 'depth', 'vertical_uncertainty', 'beam',
            'rejected', 'system', 'linename'
        vertical_exaggeration
            multiplier for z value
        view_direction
            picks either northings or eastings for display, only for 2d view
        show_axis
            to build or not build the axis

        Returns
        -------
        matplotlib.colors.ColorMap
            cmap object that we use later to build the color bar
        float
            minimum value to use for the color bar
        float
            maximum value to use for the color bar
        """

        if not self.z.any():
            return None, None, None

        self._configure_2d_3d_view()
        self.view_direction = view_direction
        self.vertical_exaggeration = vertical_exaggeration
        self.show_axis = show_axis

        self._build_statistics()
        # we need to subtract the min of our arrays.  There is a known issue with vispy (maybe in opengl in general) that large
        # values (like northings/eastings) cause floating point problems and the point positions jitter as you move
        # the camera (as successive redraw commands are run).  By zero centering and saving the offset, we can display
        # the centered northing/easting and rebuild the original value by adding the offset back in if we need to
        self.x_offset = self.min_x
        self.y_offset = self.min_y
        self.z_offset = self.min_z
        centered_z = self.z - self.z_offset

        if view_direction in ['north', 'east', 'top']:
            centered_x = self.x - self.x_offset
            centered_y = self.y - self.y_offset
        else:
            centered_x = self.rotx - self.x_offset
            centered_y = self.roty - self.y_offset

        # camera assumes z is positive up, flip the values
        if self.is_3d:
            centered_z = (centered_z - centered_z.max()) * -1 * vertical_exaggeration
        else:
            centered_z = centered_z * -1

        self.displayed_points = np.stack([centered_x, centered_y, centered_z], axis=1)
        clrs, cmap, minval, maxval = self._build_color_by_soundings(color_by)

        self.scatter = scene.visuals.Markers(parent=self.view.scene)
        # still need to figure this out.  Disabling depth test handles the whole plot-is-dark-from-one-angle,
        #   but you lose the intelligence it seems to have with depth of field of view, where stuff shows up in front
        #   of other stuff.  For now we just deal with the darkness issue, and leave depth_test=True (the default).
        if self.is_3d:
            self.scatter.set_gl_state(depth_test=True, blend=True, blend_func=('src_alpha', 'one_minus_src_alpha'))  # default
        else:  # two d we dont need to worry about the field of view, we can just show all as a blob
            self.scatter.set_gl_state(depth_test=False, blend=True, blend_func=('src_alpha', 'one_minus_src_alpha'))

        self._build_scatter(clrs)
        self.setup_axes()

        return cmap, minval, maxval

    def highlight_selected_scatter(self, color_by):
        """
        A quick highlight method that circumvents the slower set_data.  Simply set the new colors and update the data.
        """

        clrs, cmap, minval, maxval = self._build_color_by_soundings(color_by)
        self.scatter._data['a_fg_color'] = clrs
        self.scatter._data['a_bg_color'] = clrs
        self.scatter._vbo.set_data(self.scatter._data)
        self.scatter.update()
        return cmap, minval, maxval

    def clear_display(self):
        """
        Have to clear the scatterplot each time we update the display, do so by setting the parent of the plot to None
        """
        if self.scatter is not None:
            # By setting the scatter visual parent to None, we delete it (clearing the widget)
            self.scatter.parent = None
            self.scatter = None
        if self.twod_grid:
            self.canvas.central_widget.remove_widget(self.twod_grid)
        self.twod_grid = None

    def clear(self):
        """
        Clear display and all stored data
        """
        self.clear_display()
        self.id = np.array([])
        self.head = np.array([])
        self.x = np.array([])
        self.y = np.array([])
        self.z = np.array([])
        self.rotx = np.array([])
        self.roty = np.array([])
        self.tvu = np.array([])
        self.rejected = np.array([])
        self.pointtime = np.array([])
        self.beam = np.array([])
        self.linename = np.array([])
        if self.axis_x is not None:
            self.axis_x.parent = None
            self.axis_x = None
        if self.axis_y is not None:
            self.axis_y.parent = None
            self.axis_y = None
        if self.axis_z is not None:
            self.axis_z.parent = None
            self.axis_z = None
        if self.twod_grid:
            self.canvas.central_widget.remove_widget(self.twod_grid)
        self.twod_grid = None


class ThreeDWidget(QtWidgets.QWidget):
    """
    Widget containing the OptionsWidget (left pane) and VesselView (right pane).  Manages the signals that connect the
    two widgets.
    """

    points_selected = Signal(object, object, object, object, object, object, object, object, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.three_d_window = ThreeDView(self)

        self.mainlayout = QtWidgets.QVBoxLayout()

        self.opts_layout = QtWidgets.QHBoxLayout()
        self.colorby_label = QtWidgets.QLabel('Color By: ')
        self.opts_layout.addWidget(self.colorby_label)
        self.colorby = QtWidgets.QComboBox()
        self.colorby.addItems(['depth', 'vertical_uncertainty', 'beam', 'rejected', 'system', 'linename'])
        self.colorby.setToolTip('Attribute used to color the soundings, see the colorbar for values')
        self.opts_layout.addWidget(self.colorby)
        self.viewdirection_label = QtWidgets.QLabel('View Direction: ')
        self.opts_layout.addWidget(self.viewdirection_label)
        self.viewdirection = QtWidgets.QComboBox()
        self.viewdirection.addItems(['north', 'east', 'top', 'arrow'])
        self.viewdirection.setToolTip('View direction shown in the Points View:\n\n' +
                                      'north - in 2d, this will show the eastings (x) vs depth\n' +
                                      'east - in 2d, this will show the northings (y) vs depth\n' +
                                      'top - in 3d, this will show the unrotated soundings (soundings as is)\n' +
                                      'arrow - in 2d, this will show the rotated soundings looking down the direction shown as the arrow of the box select tool in 2dview\n' +
                                      '      - in 3d, this will show the rotated soundings in a top view, using the direction shown as the arrow of the box select tool in 2dview')
        self.opts_layout.addWidget(self.viewdirection)
        self.vertexag_label = QtWidgets.QLabel('Vertical Exaggeration: ')
        self.opts_layout.addWidget(self.vertexag_label)
        self.vertexag = QtWidgets.QDoubleSpinBox()
        self.vertexag.setMaximum(99.0)
        self.vertexag.setMinimum(1.0)
        self.vertexag.setSingleStep(0.5)
        self.vertexag.setValue(1.0)
        self.vertexag.setToolTip('Multiplier used for the depth values, displayed z is multiplied by this number')
        self.opts_layout.addWidget(self.vertexag)
        self.show_axis = QtWidgets.QCheckBox('Show Axis')
        self.show_axis.setChecked(True)
        self.opts_layout.addWidget(self.show_axis)
        self.show_colorbar = QtWidgets.QCheckBox('Show Colorbar')
        self.show_colorbar.setChecked(True)
        self.opts_layout.addWidget(self.show_colorbar)
        self.opts_layout.addStretch()

        self.colorbar = ColorBar()

        self.viewlayout = QtWidgets.QHBoxLayout()
        self.viewlayout.addWidget(self.three_d_window)
        self.viewlayout.addWidget(self.colorbar)
        self.viewlayout.setStretchFactor(self.three_d_window, 1)
        self.viewlayout.setStretchFactor(self.colorbar, 0)

        self.mainlayout.addLayout(self.opts_layout)
        self.mainlayout.addLayout(self.viewlayout)

        instruct = 'Left Mouse Button: Rotate,  Right Mouse Button/Mouse wheel: Zoom,  Shift + Left Mouse Button: Move,'
        instruct += ' Ctrl + Left Mouse Button: Query'
        self.instructions = QtWidgets.QLabel(instruct)
        self.instructions.setAlignment(QtCore.Qt.AlignCenter)
        self.instructions.setStyleSheet("QLabel { font-style : italic }")
        self.instructions.setWordWrap(True)
        self.mainlayout.addWidget(self.instructions)

        self.mainlayout.setStretchFactor(self.viewlayout, 1)
        self.mainlayout.setStretchFactor(self.instructions, 0)
        self.setLayout(self.mainlayout)

        self.colorby.currentTextChanged.connect(self.change_color_by)
        self.viewdirection.currentTextChanged.connect(self.refresh_settings)
        self.vertexag.valueChanged.connect(self.refresh_settings)
        self.show_axis.stateChanged.connect(self.refresh_settings)
        self.show_colorbar.stateChanged.connect(self._event_show_colorbar)

    def _event_show_colorbar(self, e):
        """
        On checking the show colorbar, we show/hide the colorbar
        """
        show_colorbar = self.show_colorbar.isChecked()
        if show_colorbar:
            self.colorbar.show()
        else:
            self.colorbar.hide()

    def add_points(self, head: np.array, x: np.array, y: np.array, z: np.array, tvu: np.array, rejected: np.array, pointtime: np.array,
                   beam: np.array, newid: str, linename: np.array, is_3d: bool, azimuth: float = None):
        """
        Adding new points will update the three d window with the boints and set the controls to show/hide
        """
        if is_3d:
            self.viewdirection.clear()
            self.viewdirection.addItems(['top', 'arrow'])
            self.show_axis.hide()
            self.vertexag_label.show()
            self.vertexag.show()
        else:
            self.viewdirection.clear()
            self.viewdirection.addItems(['north', 'east', 'arrow'])
            self.show_axis.hide()
            self.vertexag_label.hide()
            self.vertexag.hide()
        self.three_d_window.selected_points = None
        self.three_d_window.superselected_index = None
        self.three_d_window.add_points(head, x, y, z, tvu, rejected, pointtime, beam, newid, linename, is_3d, azimuth=azimuth)

    def return_points(self):
        return self.three_d_window.return_points()

    def select_points(self, startpos, endpos, three_d: bool = False):
        """
        Triggers when the user CTRL+Mouse selects data in the 3dview.  We set the selected points and let the view know
        to highlight those points and set the attributes in the Kluster explorer widget.
        """

        vd = self.viewdirection.currentText()
        if three_d:
            mask_x_min = self.three_d_window.displayed_points[:, 0] >= np.min([startpos[0][0], startpos[1][0], endpos[0][0], endpos[1][0]])
            mask_x_max = self.three_d_window.displayed_points[:, 0] <= np.max([startpos[0][0], startpos[1][0], endpos[0][0], endpos[1][0]])
            mask_y_min = self.three_d_window.displayed_points[:, 1] >= np.min([startpos[0][1], startpos[1][1], endpos[0][1], endpos[1][1]])
            mask_y_max = self.three_d_window.displayed_points[:, 1] <= np.max([startpos[0][1], startpos[1][1], endpos[0][1], endpos[1][1]])
            points_in_screen = np.argwhere(mask_x_min & mask_x_max & mask_y_min & mask_y_max)
            self.three_d_window.selected_points = points_in_screen[:, 0]
        else:
            if vd in ['north']:
                m1 = self.three_d_window.displayed_points[:, [0, 2]] >= startpos[0:2]
                m2 = self.three_d_window.displayed_points[:, [0, 2]] <= endpos[0:2]
            elif vd in ['east', 'arrow']:
                m1 = self.three_d_window.displayed_points[:, [1, 2]] >= startpos[0:2]
                m2 = self.three_d_window.displayed_points[:, [1, 2]] <= endpos[0:2]
            self.three_d_window.selected_points = np.argwhere(m1[:, 0] & m1[:, 1] & m2[:, 0] & m2[:, 1])[:, 0]
        self.points_selected.emit(np.arange(self.three_d_window.selected_points.shape[0]),
                                  self.three_d_window.linename[self.three_d_window.selected_points],
                                  self.three_d_window.pointtime[self.three_d_window.selected_points],
                                  self.three_d_window.beam[self.three_d_window.selected_points],
                                  self.three_d_window.x[self.three_d_window.selected_points],
                                  self.three_d_window.y[self.three_d_window.selected_points],
                                  self.three_d_window.z[self.three_d_window.selected_points],
                                  self.three_d_window.tvu[self.three_d_window.selected_points],
                                  self.three_d_window.rejected[self.three_d_window.selected_points],
                                  self.three_d_window.id[self.three_d_window.selected_points])
        self.three_d_window.highlight_selected_scatter(self.colorby.currentText())

    def superselect_point(self, superselect_index):
        """
        Clicking on a row in Kluster explorer tells the 3dview to super-select that point, highlighting it white
        in the 3d view
        """

        self.three_d_window.superselected_index = superselect_index
        self.three_d_window.highlight_selected_scatter(self.colorby.currentText())

    def change_colormap(self, cmap, minval, maxval):
        """
        on adding new points or changing the colorby, we update the colorbar
        """

        if self.colorby.currentText() == 'rejected':
            self.colorbar.setup_colorbar(cmap, minval, maxval, is_rejected=True)
        elif self.colorby.currentText() == 'system':
            self.colorbar.setup_colorbar(cmap, minval, maxval,
                                         by_name=self.three_d_window.unique_systems)
        elif self.colorby.currentText() == 'linename':
            self.colorbar.setup_colorbar(cmap, minval, maxval,
                                         by_name=self.three_d_window.unique_linenames)
        else:
            inverty = False
            if self.colorby.currentText() == 'depth':
                inverty = True
            self.colorbar.setup_colorbar(cmap, minval, maxval, invert_y=inverty)

    def change_color_by(self, e):
        """
        Triggers on a new dropdown in colorby
        """
        cmap, minval, maxval = self.three_d_window.highlight_selected_scatter(self.colorby.currentText())
        if cmap is not None:
            self.change_colormap(cmap, minval, maxval)

    def display_points(self):
        """
        After adding points, we trigger the display by running display_points
        """
        showaxis = True  # freezes when hiding axes on 3d for some reason
        if self.vertexag.isHidden():
            vertexag = 1
        else:
            vertexag = self.vertexag.value()
        cmap, minval, maxval = self.three_d_window.display_points(color_by=self.colorby.currentText(),
                                                                  vertical_exaggeration=vertexag,
                                                                  view_direction=self.viewdirection.currentText(),
                                                                  show_axis=showaxis)
        if cmap is not None:
            self.change_colormap(cmap, minval, maxval)

    def refresh_settings(self, e):
        """
        After any substantial change to the point data or scale, we clear and redraw the points
        """
        self.clear_display()
        self.display_points()

    def clear_display(self):
        self.three_d_window.clear_display()

    def clear(self):
        self.three_d_window.clear()


def normalized_arr_to_rgb_v2(z_array_normalized: np.array, reverse: bool = False, colormap: str = 'rainbow',
                             band_count: int = 50):
    """
    Build an RGB array from a normalized input array.  Colormap will be a string identifier that can be parsed by
    the matplotlib colormap object.

    Parameters
    ----------
    z_array_normalized
        1d array that you want to convert to an RGB array, values should be between 0 and 1
    reverse
        if true, reverses the colormap
    colormap
        string identifier that is accepted by matplotlib
    band_count
        number of color bands to pull from the colormap

    Returns
    -------
    np.array
        (n,4) array, where n is the length of z_array_normalized
    matplotlib.colors.ColorMap
        cmap object that we use later to build the color bar
    """
    if reverse:
        cmap = cm.get_cmap(colormap + '_r', band_count)
    else:
        cmap = cm.get_cmap(colormap, band_count)
    return cmap(z_array_normalized), cmap


if __name__ == '__main__':
    if qgis_enabled:
        app = qgis_core.QgsApplication([], True)
        app.initQgis()
    else:
        try:  # pyside2
            app = QtWidgets.QApplication()
        except TypeError:  # pyqt5
            app = QtWidgets.QApplication([])

    win = ThreeDWidget()
    x = np.arange(10)
    y = np.arange(10)
    z = np.arange(10)
    xx, yy, zz = np.meshgrid(x, y, z)
    x = xx.ravel()
    y = yy.ravel()
    z = zz.ravel()
    tvu = np.random.rand(x.shape[0])
    rejected = np.random.randint(0, 3, size=x.shape[0])
    pointtime = np.arange(x.shape[0])
    beam = np.random.randint(0, 399, size=x.shape[0])
    linename = np.full(x.shape[0], '')
    newid = 'test'
    win.add_points(x, y, z, tvu, rejected, pointtime, beam, newid, linename, is_3d=True)
    win.display_points()
    win.show()
    app.exec_()
