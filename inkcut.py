#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# App: Inkcut
# File: inkcut.py
# Author: Copyright 2011 Jairus Martin <frmdstryr@gmail.com>
# Date: 15 Aug 2011
#
# License:
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA 02110-1301, USA.
import argparse
import sys, os, traceback, platform
import logging,logging.config
from gi.repository import Gtk, GObject,Gdk
import ConfigParser
import tempfile
from lxml import etree
from sqlalchemy import create_engine

# Path Globals
APP_DIR = os.path.abspath(os.path.dirname(__file__))
sys.path.append(os.path.join(APP_DIR,'lib'))
sys.path.append(APP_DIR)

# Inkcut modules
from meta import Session
from job import Job
from material import Material
from device import Device
from unit import UNITS
import hpgl as HPGL

# Load Configuration Files
config = ConfigParser.RawConfigParser()
CONFIG_FILE = os.path.join(APP_DIR,'conf','app.cfg')
config.read(CONFIG_FILE)

# Logging
LEVELS = {'debug': logging.DEBUG,
          'info': logging.INFO,
          'warning': logging.WARNING,
          'error': logging.ERROR,
          'critical': logging.CRITICAL}
logging.basicConfig(
    filename=config.get('Inkcut','logging_filename'),
    filemode='w',
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=LEVELS.get(config.get('Inkcut','logging_level').lower(),logging.NOTSET)
    )
log = logging.getLogger(__name__)
log.info("App Dir: %s"%APP_DIR)
log.info("Config Dir: %s"%CONFIG_FILE)

PLUGIN_EXTENSIONS = ["*.hpgl","*.gpgl"]

class Application():
    """
    Class that contains methods for keeping track of widgets imported
    using the Gtk.Builder.  Includes shared UI functions and methods.
    """
    def __init__(self):
        #Gtk.Application.__init__(self,
        #    application_id="app.inkcut",
        #    flags=Gio.ApplicationFlags.FLAGS_NONE)
        #self.connect("activate",self.on_activate)

        # ========== This Block is not needed if using Gtk.Application
        self._windows = {}
        # ========== End of block ====================================
        self._widgets = {}

    def add_window(self,window,name=None):
        """ Adds a window to the Application """
        if name is None:
            name = window.get_name()
        self._windows[name] = window

    def get_window(self,name):
        """ Gets a window with the given name """
        return self._windows[name]

    def add_widgets(self,builder,group,names):
        """Saves widgets with name in the list names into a group."""
        if type(names) == str:
            names = list(names)
        for name in names:
            widget = builder.get_object(name)
            self.add_widget(widget,group,name)

    def add_widget(self,widget,group,name):
        """Saves widgets with name in the list names into a group. Doesn't require a builder."""
        if group not in self._widgets.keys():
            self._widgets[group]={}
        if widget:
            self._widgets[group][name] = widget

    def get_widget(self,group,name):
        """ Retrieves widget from widget group """
        return self._widgets[group][name]

    def get_widgets(self,group):
        """ Retrieves all widgets from group """
        assert group in self._widgets.keys(), "There is no widget group: %s" % group
        return self._widgets[group]

    # Builder Helpers
    @staticmethod
    def set_adjustment_values(builder,etree):
        """ Glade default adjustment values fix """
        for object in etree.xpath('/interface/object[@class="GtkAdjustment"]'):
            property = object.xpath('property[@name="value"]')
            if len(property):
                obj = builder.get_object(object.get('id'))
                obj.set_value(float(property[0].text))

    @staticmethod
    def get_combobox_active_text(combobox):
       model = combobox.get_model()
       active = combobox.get_active()
       if active < 0:
          return None
       return model[active][0]

    @staticmethod
    def set_model_from_list (cb, items):
        """Setup a ComboBox or ComboBoxEntry based on a list of strings."""
        model = Gtk.ListStore(str)
        for i in items:
            model.append([i])
        cb.set_model(model)
        if type(cb) == Gtk.ComboBoxText:
            cb.set_text_column(0)
        elif type(cb) == Gtk.ComboBox:
            cell = Gtk.CellRendererText()
            cb.pack_start(cell, True)
            cb.add_attribute(cell, 'text', 0)

def callback(fn):
    """
    Checks if the field should be updated.
    Catches errors and sends them to a UI message window.
    """
    def wrapped(self,*args):

        msg = "callback: %s, blocked: %s"%(fn.__name__,self._flags['block_callbacks'])
        print msg
        log.debug(msg)
        if self._flags['block_callbacks']:
            return None
        else: # call by default, only block if false
            if self.job is not None:
                self.get_window('inkcut').set_title("*%s - Inkcut"%self.job.name)
            fn(self,*args)
        """
        TODO: This needs to be moved somewhere to catch exceptions and send
        messages to the UI...
            log.debug(traceback.format_exc())
            msg = Gtk.MessageDialog(type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                message_format="Error")
            msg.format_secondary_text("Fricking iditio")
            msg.run()
            msg.destroy()
        """
    return wrapped

class Inkcut(Application):
    """
    Inkcut application, creates inkcut.ui and controls job, material,
    and device interaction.
    """

    def __init__(self):
        """Load initial application settings from database """
        Application.__init__(self)

        # setup the database session
        database = 'sqlite:///%s'%os.path.join(APP_DIR,config.get('Inkcut','database_dir'),config.get('Inkcut','database_name'))
        log.info("Database: %s"%database)
        engine = create_engine(database)
        Session.configure(bind=engine)
        self.session = Session()
        self.job = None
        self._flags = {'block_callbacks':True}

    # Builds the Inkcut user interface when Inkcut.run() is called
    def run(self, filename=None):
        builder = Gtk.Builder()
        builder.add_from_file(os.path.join(APP_DIR,'ui','inkcut.ui'))
        window = builder.get_object('inkcut')

        # Save any widgets needed later into groups
        self.add_widgets(builder,'job-dependent',['toolbutton3','toolbutton5','toolbutton6','box21','box9','box11','box3','box15','box25','box28','plot-order','box17','menu-file-print-preview','menu-file-print','menu-edit-undo','menu-edit-redo','gtk-zoom-fit','gtk-zoom-in','gtk-zoom-out','menu-file-save-as','menu-file-save'])
        self.add_widgets(builder,'inkcut',['inkcut','devices','statusbar','preview','scale-lock-box','scale-x-label','spinner'])
        self.add_widgets(builder,'graphic-properties',['graphic-width','graphic-height','graphic-scale-x','graphic-scale-y','graphic-scale-lock','graphic-rotation','graphic-rotate-to-save','graphic-weedline-enable','graphic-weedline-padding'])
        self.add_widgets(builder,'plot-properties',['plot-width','plot-height','plot-copies','plot-weedline-enable','plot-weedline-padding','plot-col-spacing','plot-row-spacing','plot-position-x','plot-position-y','plot-feed','plot-feed-distance'])
        self.add_widgets(builder,'material',['materials'])


        # Connect signals and invoke any UI setup
        builder.connect_signals(self)

        model = ['0 Degrees Clockwise','90 Degrees Clockwise','180 Degrees Clockwise','270 Degrees Clockwise']
        combobox = builder.get_object('graphic-rotation')
        self.set_model_from_list(combobox,model)
        combobox.set_active(0)

        model = ['One Copy at a Time','Best Tracking','Shortest Path']
        combobox = builder.get_object('plot-order')
        self.set_model_from_list(combobox,model)
        combobox.set_active(0)

        # Based on SQL Table: id, name, cost, width, length, margin_top, margin_right, margin_bottom, margin_left, velocity, force, color
        active = 0
        materials = Gtk.ListStore(int,str)
        for m in self.session.query(Material).all():
            materials.append([m.id,m.name])
            if m.id == int(config.get('Inkcut','default_device')):
                active = len(materials)
        combobox = self.get_widget('material','materials')
        combobox.set_model(materials)
        cell = Gtk.CellRendererText()
        combobox.pack_start(cell, True)
        combobox.add_attribute(cell, 'text', 1)
        combobox.set_active(active)


        model = []
        device_active_index = 0
        for device in Device.get_printers():
            name = device.name
            if device.id == int(config.get('Inkcut','default_device')):
                name += " (default)"
                device_active_index = len(model)
            model.append(name)
            log.info(name)
        combobox = builder.get_object('devices')
        self.set_model_from_list(combobox,model)
        combobox.set_active(device_active_index)



        # Init Accelerators
        accel_group = builder.get_object('accelgroup1')
        for action in builder.get_object('actiongroup1').list_actions():
            action.set_accel_group(accel_group)
            action.connect_accelerator()
        window.add_accel_group(accel_group)

        # Show the widgets
        self._update_sensitivity()
        window.show_all()
        self.flash("",indicator=False)

        # Fix!
        self._flags['block_callbacks']=False
        self.add_window(window,'inkcut')
        self.on_graphic_scale_lock_toggled(self.get_widget('graphic-properties','graphic-scale-lock'))
        # From command line arguments
        if filename is not None and os.path.isfile(filename):
            self.create_job(filename)

        Gtk.main()

    # ===================================== Plot Callbacks ===============================================
    @callback
    def on_plot_feed_distance_changed(self,radio,data=None):
        self.flash("Saving the plot feeding settings...",indicator=True)
        feed = self.get_widget('plot-properties','plot-feed').get_active()
        if feed:
            d = self.unit_to(self.get_widget('plot-properties','plot-feed-distance').get_value())
            pos = (0,d)
            if self.job.plot.get_rotation() == 90:
                pos = (d,0)
            self.job.set_property('plot','finish_position',pos)
        else:
            self.job.set_property('plot','finish_position',(0,0))
        GObject.timeout_add(1000,self.flash,"")

    @callback
    def on_plot_copies_changed(self,adjustment,data=None):
        """Set's the plot's copies to the adjustment value. """
        n = int(adjustment.get_value())
        self.flash("Setting the number of copies to %s..."%n,indicator=True)
        GObject.idle_add(self.job.plot.set_copies,n)
        GObject.idle_add(self._update_preview)

    @callback
    def on_stack_reset_activated(self,button, data=None):
        """Sets the graphic-copies to 1"""
        self.get_widget('plot-properties','plot-copies').set_value(1)
        # Preview updated by on_plot_copies_changed

    @callback
    def on_stack_add_activated(self,button, data=None):
        """Adds a full stack of copies"""
        stack = self.job.plot.get_stack_size_x()
        copies = self.job.plot.get_copies()
        self.get_widget('plot-properties','plot-copies').set_value(stack*(copies/stack+1))
        # Preview updated by on_plot_copies_changed

    @callback
    def on_plot_spacing_x_changed(self,adjustment,data=None):
        x = self.unit_to(adjustment.get_value())
        if self.get_widget('plot-properties','plot-copies').get_value() > 1:
            msg = "Updating the column spacing..."
        else:
            msg = "Saving the plot column spacing..."
        self.flash(msg,indicator=True)
        GObject.idle_add(self.job.plot.set_spacing,x,None)
        GObject.idle_add(self._update_preview)

    @callback
    def on_plot_spacing_y_changed(self,adjustment,data=None):
        y = self.unit_to(adjustment.get_value())
        if self.get_widget('plot-properties','plot-copies').get_value() > 1:
            msg = "Updating the row spacing..."
        else:
            msg = "Saving the plot row spacing..."
        self.flash(msg,indicator=True)
        GObject.idle_add(self.job.plot.set_spacing,None,y)
        GObject.idle_add(self._update_preview)

    @callback
    def on_plot_position_x_changed(self,widget,data=None):
        x = self.unit_to(widget.get_value())
        pos = self.job.plot.get_position()
        self.flash("Moving the plot to (%s,%s)"%(x,pos[1]),indicator=True)
        GObject.idle_add(self.job.plot.set_position,x,pos[1])
        GObject.idle_add(self._update_preview)

    @callback
    def on_plot_position_y_changed(self,widget,data=None):
        y = self.unit_to(widget.get_value())
        pos = self.job.plot.get_position()
        self.flash("Moving the plot to (%s,%s)"%(pos[0],y),indicator=True)
        GObject.idle_add(self.job.plot.set_position,pos[0],y)
        GObject.idle_add(self._update_preview)

    @callback
    def on_plot_center_x_toggled(self,checkbox,data=None):
        enabled = checkbox.get_active()
        if enabled:
            msg = "Centering the plot horizontally..."
        else:
            msg = "Moving the plot to the horizontal start..."
        self.flash(msg,indicator=True)
        GObject.idle_add(self.job.plot.set_align_center_x,enabled)
        GObject.idle_add(self._update_plot_ui)

    @callback
    def on_plot_center_y_toggled(self,checkbox,data=None):
        enabled = checkbox.get_active()
        if enabled:
            msg = "Centering the plot vertically..."
        else:
            msg = "Moving the plot to the vertical start..."
        self.flash(msg,indicator=True)
        GObject.idle_add(self.job.plot.set_align_center_y,enabled)
        GObject.idle_add(self._update_plot_ui)

    @callback
    def on_plot_weedline_toggled(self,checkbox,data=None):
        enabled = checkbox.get_active()
        if enabled:
            msg = "Adding a weedline to the plot..."
        else:
            msg = "Removing the plot weedline..."
        self.flash(msg,indicator=True)
        GObject.idle_add(self.job.plot.set_weedline,enabled)
        GObject.idle_add(self._update_preview)

    @callback
    def on_plot_weedline_padding_changed(self,adjustment,data=None):
        if self.get_widget('plot-properties','plot-weedline-enable').get_active():
            msg = "Updating the plot weedline padding..."
        else:
            msg = "Saving the plot weedline padding..."
        self.flash(msg,indicator=True)
        GObject.idle_add(self.job.plot.set_weedline_padding,self.unit_to(adjustment.get_value()))
        GObject.idle_add(self._update_preview)

    # ===================================== Graphic Callbacks ===============================================
    @callback
    def on_graphic_width_changed(self,adjustment,data=None):
        # Calculate new scale
        cur_w = self.job.plot.graphic.get_width()
        new_w = float(self.unit_to(adjustment.get_value()))
        sx,sy = self.job.plot.graphic.get_scale()
        sx = new_w/cur_w*sx
        # Updated in on_graphic_scale_x_changed callback
        self.get_widget('graphic-properties','graphic-scale-x').set_value(sx)

    @callback
    def on_graphic_height_changed(self,adjustment,data=None):
        # Calculate new scale
        cur = self.job.plot.graphic.get_height()
        new = float(self.unit_to(adjustment.get_value()))
        sx,sy = self.job.plot.graphic.get_scale()
        s = new/cur*sy
        # Updated in on_graphic_scale_x_changed callback
        self.get_widget('graphic-properties','graphic-scale-y').set_value(s)

    @callback
    def on_graphic_scale_y_changed(self,adjustment,data=None):
        sy = adjustment.get_value()
        if self.get_widget('graphic-properties','graphic-scale-lock').get_active():
            sx = sy
        else:
            sx = self.get_widget('graphic-properties','graphic-scale-x').get_value()
        GObject.idle_add(self.job.plot.graphic.set_scale,sx,sy)
        GObject.idle_add(self._update_ui)

    @callback
    def on_graphic_scale_x_changed(self,adjustment,data=None):
        sx = adjustment.get_value()
        if self.get_widget('graphic-properties','graphic-scale-lock').get_active():
            sy = sx
        else:
            sy = self.get_widget('graphic-properties','graphic-scale-y').get_value()
        GObject.idle_add(self.job.plot.graphic.set_scale,sx,sy)
        GObject.idle_add(self._update_ui)

    @callback
    def on_graphic_rotate_to_save_toggled(self,checkbox,data=None):
        self.flash("Checking and updating...",indicator=True)
        GObject.idle_add(self.job.plot.set_auto_rotate,checkbox.get_active())
        GObject.idle_add(self._update_preview)

    @callback
    def on_graphic_weedline_padding_changed(self,adjustment,data=None):
        if self.get_widget('graphic-properties','graphic-weedline-enable').get_active():
            msg = "Updating the graphic weedline padding..."
        else:
            msg = "Saving the graphic weedline padding..."
        self.flash(msg,indicator=True)
        GObject.idle_add(self.job.plot.graphic.set_weedline_padding,self.unit_to(adjustment.get_value()))
        GObject.idle_add(self._update_graphic_ui)

    @callback
    def on_graphic_weedline_toggled(self,checkbox,data=None):
        enabled = checkbox.get_active()
        if enabled:
            msg = "Adding a weedline to the graphic..."
        else:
            msg = "Removing the graphic weedline..."
        self.flash(msg,indicator=True)
        GObject.idle_add(self.job.plot.graphic.set_weedline,enabled)
        GObject.idle_add(self._update_preview)

    @callback
    def on_graphic_mirror_x_toggled(self,checkbox,data=None):
        enabled = checkbox.get_active()
        if enabled:
            msg = "Mirroring graphic about the y-axis..."
        else:
            msg = "Returning graphic to original mirror state..."
        self.flash(msg,indicator=True)
        GObject.idle_add(self.job.plot.graphic.set_mirror_x,enabled)
        GObject.idle_add(self._update_preview)

    @callback
    def on_graphic_mirror_y_toggled(self,checkbox,data=None):
        enabled = checkbox.get_active()
        if enabled:
            msg = "Mirroring graphic about the x-axis..."
        else:
            msg = "Returning graphic to original mirror state..."
        self.flash(msg,indicator=True)
        GObject.idle_add(self.job.plot.graphic.set_mirror_y,enabled)
        GObject.idle_add(self._update_preview)

    @callback
    def on_graphic_scale_lock_toggled(self,checkbox,data=None):
        if checkbox.get_active():
            self.get_widget('inkcut','scale-x-label').set_text("Scale:")
            self.get_widget('inkcut','scale-lock-box').hide()
            if self.job is not None:
                s = self.get_widget('graphic-properties','graphic-scale-x').get_value()
                self.get_widget('graphic-properties','graphic-scale-y').set_value(s)
        else:
            self.get_widget('inkcut','scale-x-label').set_text("Scale - X:")
            self.get_widget('inkcut','scale-lock-box').show()

    @callback
    def on_graphic_rotation_changed(self, combobox, data=None):
        degrees = combobox.get_active()*90
        self.flash("Setting graphic rotation to %s..."%degrees,indicator=True)
        GObject.idle_add(self.job.plot.graphic.set_rotation,degrees)
        GObject.idle_add(self._update_graphic_ui)


    # ===================================== File Menu Callbacks ===============================================
    @callback
    def on_job_review_activated(self,action,data=None):
        self.job.update_properties()
        builder = Gtk.Builder()
        builder.add_from_file(os.path.join(APP_DIR,'ui','plot.ui'))
        dialog = builder.get_object('dialog1')
        builder.connect_signals(self)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    @callback
    def on_job_submit_activated(self,action,data=None):
        self.job.update_properties()
        GObject.idle_add(self.run_export_plugin)

    def run_export_plugin(self):
        # Write the job to a file
        tmp = tempfile.NamedTemporaryFile(suffix=".svg",delete=False)
        tmp.file.write(self.job.plot.get_xml())
        svg = tmp.name
        tmp.close()

        # Get the current device
        name = self.get_combobox_active_text(self.get_widget('inkcut','devices'))
        #device = self.session.query(Device).filter(Device.name==name).first()
        device = Device.get_printer_by_name(name)
        log.info(device)
        # Init the export plugin
        # TODO: Gather plugins here.... let user select...
        plugins = [HPGL.Import(),HPGL.Export()]
        plugin_found = False
        for plugin in plugins:
            if plugin.mode.lower() == "export":
                if str(device.command_language.lower()) in plugin.filetypes:
                    plugin_found = True
                    break
        if plugin_found == False:
            msg = Gtk.MessageDialog(type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                message_format="No plugins found for the command language:%s."%device.command_language)
            msg.format_secondary_text("Please make sure the plugins for this device are installed.\n If the problem persists, contact the developer.")
            msg.run()
            msg.destroy()
            raise Exception("Error, no plugin found for that command language...")

        # Initialize the plugin then run it
        # TODO: put everything needed here...
        plugin.export_init(plot=self.job.get_properties('plot'),
            device={
                'cutting_overlap':(10,False)
                })
        plugin.input = svg
        plugin.run()
        device.submit(plugin.output)

        # Cleanup
        os.remove(svg)

    def on_file_save_action_activated(self,widget,data=None):
        self.flash("Saving %s into the Job database..."%(self.job.name),indicator=True)
        self.job.update_properties()
        self.get_window('inkcut').set_title("%s - Inkcut"%self.job.name)
        self.session.commit()
        GObject.timeout_add(1000,self.flash,"")

    def create_job(self,filename,**kwargs):
        """Create a job and try to set the source. Returns bool success."""
        job = Job(**kwargs)
        # Get the default material
        job.material = self.get_material()
        try:
            job.set_source(filename)
            self.job = job
            self.session.add(self.job)
            msg = 'Loaded %s'%os.path.basename(job.name or 'File')
            self.get_window('inkcut').set_title("*%s - Inkcut"%job.name)
            self.flash(msg)
            self.on_plot_feed_distance_changed(self.get_widget('plot-properties','plot-feed'))
            self._update_ui()
            return False
        except Exception, err:
            # update the ui with job info
            log.debug(traceback.format_exc())
            msg = Gtk.MessageDialog(type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                message_format="Issue loading file")
            msg.format_secondary_text(err)
            msg.run()
            msg.destroy()
            return False
        except:
            # update the ui with job info
            log.debug(traceback.format_exc())
            msg = Gtk.MessageDialog(type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                message_format="Unexpected Error: %s"%(err))
            msg.format_secondary_text("Please try again.  If the problem persists, contact the developer for more help.")
            msg.run()
            msg.destroy()
            return False

    def on_file_open_action_activated(self,widget,data=None):
        """
        Displays a file chooser dialog. When a file is selected, a new
        job is created using the file.
        """
        dialog = Gtk.FileChooserDialog(title='Open File - Inkcut',action=Gtk.FileChooserAction.OPEN,
                    buttons=(Gtk.STOCK_CANCEL,Gtk.ResponseType.CANCEL,Gtk.STOCK_OPEN,Gtk.ResponseType.OK))
        last_folder = config.get('Inkcut','last_file_open_dir') or os.getenv('USERPROFILE') or os.getenv('HOME')
        dialog.set_current_folder(last_folder)

        filter = Gtk.FileFilter()
        filter.set_name("SVG Images")
        filter.add_mime_type("image/svg+xml")
        filter.add_pattern("*.svg")
        dialog.add_filter(filter)

        filter = Gtk.FileFilter()
        filter.set_name("All files")
        filter.add_pattern("*")
        dialog.add_filter(filter)

        # Todo, set these from plugins
        filter = Gtk.FileFilter()
        filter.set_name("Plots")
        for pattern in PLUGIN_EXTENSIONS:
            filter.add_pattern(pattern)
        dialog.add_filter(filter)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            last_folder = os.path.abspath(os.path.dirname(filename))
            config.set('Inkcut','last_file_open_dir',last_folder)
            log.debug("Set last_file_open_dir = %s"%last_folder)
            msg = "Opening file: %s..."%filename
            self.flash(msg,indicator=True)
            GObject.idle_add(self.create_job,filename)
        dialog.destroy()

    def on_file_exit(self,widget):
        # Kill GTK window
        Gtk.main_quit()
        # Should follow procedures in __init__ to kill

    # ===================================== Updating UI ===============================================

    def _update_sensitivity(self):
        """ Set's the senstivity to widgets based on the if a Job exists.  """
        if self.job is None:
            for widget in self.get_widgets('job-dependent').values():
                widget.set_sensitive(False)
        else:
            for widget in self.get_widgets('job-dependent').values():
                widget.set_sensitive(True)

    def _update_ui(self):
        """ Updates all widgets and the preview, should only be used when all adjustments need reset. """
        self._update_sensitivity()
        # Block all signals, because we don't want them to be called while updating values
        self._flags['block_callbacks'] = True

        self._update_graphic_ui(preview=False)
        self._update_plot_ui(preview=False)

        # Unblock all signals, because we don't want them to be called while updating values
        self._flags['block_callbacks'] = False

        self._update_preview()

    def _update_graphic_ui(self,preview=True):
        """ Update all the Graphic Related widgets... and the preview."""
        adjustment = self.get_widget('graphic-properties','graphic-width')
        adjustment.set_value(self.unit_from(self.job.plot.graphic.get_width()))
        adjustment = self.get_widget('graphic-properties','graphic-height')
        adjustment.set_value(self.unit_from(self.job.plot.graphic.get_height()))
        adjustment = self.get_widget('graphic-properties','graphic-scale-x')
        adjustment.set_value(self.job.plot.graphic.get_scale()[0])
        adjustment = self.get_widget('graphic-properties','graphic-scale-y')
        adjustment.set_value(self.job.plot.graphic.get_scale()[1])
        combobox = self.get_widget('graphic-properties','graphic-rotation')
        combobox.set_active(int(round(self.job.plot.graphic.get_rotation()/90)))
        if preview:
            self._update_preview()

    def _update_plot_ui(self,preview=True):
        """ Update all the Plot Related widgets... and the preview."""
        adjustment = self.get_widget('plot-properties','plot-copies')
        adjustment.set_value(self.job.plot.get_copies())
        adjustment = self.get_widget('plot-properties','plot-col-spacing')
        adjustment.set_value(self.unit_from(self.job.plot.get_spacing()[0]))
        adjustment = self.get_widget('plot-properties','plot-row-spacing')
        adjustment.set_value(self.unit_from(self.job.plot.get_spacing()[1]))
        adjustment = self.get_widget('plot-properties','plot-position-x')
        adjustment.set_value(self.unit_from(self.job.plot.get_position()[0]))
        adjustment = self.get_widget('plot-properties','plot-position-y')
        adjustment.set_value(self.unit_from(self.job.plot.get_position()[1]))
        if preview:
            self._update_preview()

    def _update_preview(self):
        """ Refreshes the preview """
        tmp = tempfile.NamedTemporaryFile(suffix=".svg",delete=False)
        tmp.file.write(self.job.plot.get_preview_xml())
        svg = tmp.name
        tmp.close()
        log.debug("Preview loaded: %s"%svg)
        self.get_widget('inkcut','preview').set_from_file(svg)
        os.remove(svg)
        GObject.timeout_add(1000,self.flash,"")
        return 0

    def flash(self,msg,duration=0,context_id=None,indicator=False):
        """ Flash a message in the statusbar for duration in s"""
        self.indicator(indicator)
        log.info(msg)
        if duration>0:
            GObject.timeout_add(duration*1000,self.flash,"")

        statusbar = self.get_widget('inkcut','statusbar')
        if context_id is None:
            context_id = statusbar.get_context_id(msg)
        statusbar.push(context_id,msg)
        return 0

    def indicator(self,enabled=True):
        spinner = self.get_widget('inkcut','spinner')
        if enabled:
            spinner.show()
            return 0
        else:
            spinner.hide()
            return 0


    # ===================================== Dialog Callbacks ===============================================
    def on_about_inkcut_action_activated(self,window,data=None):
        """ Display the about dialog """
        builder = Gtk.Builder()
        builder.add_from_file(os.path.join(APP_DIR,'ui','about.ui'))
        dialog = builder.get_object('aboutdialog1')
        dialog.show_all()
        dialog.run()
        dialog.destroy()



    # ===================================== Material Menu Callbacks ===============================================
    @callback
    def on_material_changed(self,combobox,data=None):
        if self.job is not None:
            self.flash("Updating the material settings...",indicator=True)
            GObject.idle_add(self.job.set_material,self.get_material())
            GObject.idle_add(self._update_ui)

    def on_material_add_activated(self,window,data=None):
        """ Display the material properties dialog for a new material """
        MaterialPropertiesDialog(app=self,action='add')
        self.on_material_list_changed()

    def on_material_edit_activated(self,window,id=None):
        """ Display the material properties dialog for a given material """
        m = self.session.query(Material).filter(Material.id == id).one()
        MaterialPropertiesDialog(app=self,action='edit',material=m)
        self.on_material_list_changed()

    def on_material_properties_activated(self,window,data=None):
        """ Display the material properties dialog for the currently selected material."""
        MaterialPropertiesDialog(app=self,action='edit',material=self.get_material())
        self.on_material_list_changed()

    def on_material_delete_activated(self,window,id=None):
        """ Delete a given material """
        m = self.session.query(Material).filter(Material.id == id).one()
        # TODO: Display are you sure confirmation dialog. if okay, continue
        self.session.delete(m)
        self.flash("Material: %s has been deleted.."%m.name,duration=5,indicator=True)
        self.session.commit()

    def on_material_list_activated(self,window,id=None):
        """ Display the materials manager dialog """
        MaterialManagerDialog(app=self)
        self.on_material_list_changed()

    def on_material_list_changed(self,widget=None,data=None):
        """ Update the material liststore from the database """
        combobox = self.get_widget('material','materials')
        liststore = combobox.get_model()

        # Save the current value (if any)
        active = combobox.get_active()
        if active >= 0:
            acitve = liststore[active][0]

        # Rebuild the model
        materials = self.session.query(Material).all()
        liststore.clear()
        for m in materials:
            liststore.append([m.id,m.name])
            if m.id == active:
                active = len(liststore)
        combobox.set_active(active)

    # ===================================== Common Callbacks ===============================================
    def gtk_main_quit(self, window):
        """ Quit the application """
        Gtk.main_quit()

        # Save current configuration
        with open(CONFIG_FILE, 'wb') as configfile:
            config.write(configfile)
        log.debug("Config written to: %s" %CONFIG_FILE)

    def gtk_window_hide(self,window):
        """ Hide a window """
        window.hide();

    # ===================Application Helper Functions ===================
    def get_material(self):
        """ Returns the current Material selected in the UI """
        combobox = self.get_widget('material','materials')
        liststore = combobox.get_model()
        active = combobox.get_active()
        if active >= 0:
            return self.session.query(Material).filter(Material.id == liststore[active][0]).one()
        else:
            m =  self.session.query(Material).filter(Material.id == int(config.get('Inkcut','default_device'))).one()
            if m is None:
                m = self.session.query(Material).first()
            return m

    def unit_to(self,value):
        """ Converts the given units from user units to the current application default. """
        return UNITS[config.get('Inkcut','default_units')]*value

    def unit_from(self,value):
        """ Converts the given units from user units to the current application default. """
        return value/UNITS[config.get('Inkcut','default_units')]


class MaterialPropertiesDialog():
        def __init__(self,app,action,material=None):
            """
            App should be Inkcut, this dialog needs access to the app.session and app.job.
            If action=='add', the material argument is ignored, and a new material is added to the database on save.
            If action=='edit', the material passed is edited.
            """
            self.app = app
            builder = Gtk.Builder()
            builder.add_from_file(os.path.join(APP_DIR,'ui','material.ui'))

            self.ui = {}
            widgets = ['name','cost','color','colorbutton','width','length','dialog1',
                'length-sb','roll','velocity','force','use-material','speed-sb','force-sb',
                'margin-top','margin-right','margin-bottom','margin-left','apply','ok','delete',
                ]
            for name in widgets:
                self.ui[name] = builder.get_object(name)
            # Populate the UI
            self.ui['apply'].set_sensitive(False)

            # Determine if we want to Add or Edit
            if action == 'add':
                material = Material()
                self.app.session.add(material)
                # Change the form slightly..
                self.ui['ok'].set_label('gtk-add')

            # General Tab
            if material.name:
                self.ui['name'].set_text(str(material.name))
            if material.cost:
                self.ui['cost'].set_value(material.cost)
            if material.color:
                self.ui['color'].set_text(str(material.color))
                self.ui['colorbutton'].set_color(Gdk.color_parse(material.color))

            # Setup Tab
            if material.width:
                self.ui['width'].set_value(material.width)
            if material.length:
                self.ui['length'].set_value(material.length)
            else:
                self.ui['length-sb'].set_sensitive(False)
                self.ui['roll'].set_active(True)
            self.ui['margin-top'].set_value(material.margin[0])
            self.ui['margin-right'].set_value(material.margin[1])
            self.ui['margin-bottom'].set_value(material.margin[2])
            self.ui['margin-left'].set_value(material.margin[3])

            # Features Tab
            if material.velocity or material.force:
                self.ui['use-material'].set_active(True)
                self.ui['velocity'].set_value(material.velocity)
                self.ui['force'].set_value(material.force)
            else:
                self.ui['use-material'].set_active(False)

            self.material = material
            dialog = self.ui['dialog1']
            builder.connect_signals(self)
            dialog.show_all()
            if action=='add':
                self.ui['apply'].hide()
                self.ui['delete'].hide()
            dialog.run() # throw away response


        # General Tab
        def on_name_changed(self,widget,data=None):
            self.material.name = widget.get_text()
            self.ui['apply'].set_sensitive(True)


        def on_cost_changed(self,adjustment,data=None):
            self.material.cost = adjustment.get_value()
            self.ui['apply'].set_sensitive(True)

        def on_color_changed(self,widget,data=None):
            val = widget.get_text()
            if val[0] == '#':
                if len(val) not in [4,7,10,13]:
                    # not valid!
                    return 0
            self.material.color = val
            self.ui['colorbutton'].set_color(Gdk.color_parse(self.material.color))
            self.ui['apply'].set_sensitive(True)

        def on_color_set(self,button):
            c = button.get_color().to_string()
            val = "#%s%s%s"%(c[1:3],c[5:7],c[9:11])
            self.material.color = val.upper()
            self.ui['color'].set_text(self.material.color)
            self.ui['apply'].set_sensitive(True)

        # Setup tab
        def on_width_changed(self,adjustment,data=None):
            self.material.width = adjustment.get_value()
            self.ui['apply'].set_sensitive(True)

        def on_length_changed(self,adjustment,data=None):
            self.material.length = adjustment.get_value()
            self.ui['apply'].set_sensitive(True)

        def on_roll_toggled(self,checkbox):
            if checkbox.get_active():
                self.material.length = None
                self.ui['length-sb'].set_sensitive(False)
            else:
                self.material.length = self.ui['length'].get_value()
                self.ui['length-sb'].set_sensitive(True)
            self.ui['apply'].set_sensitive(True)

        def on_margin_changed(self,adjustment,data=None):
            margin = [  self.ui['margin-top'].get_value(),
                        self.ui['margin-right'].get_value(),
                        self.ui['margin-bottom'].get_value(),
                        self.ui['margin-left'].get_value()]
            self.material.margin = margin
            self.ui['apply'].set_sensitive(True)

        # Features tab
        def on_use_material_toggled(self,radio,data=None):
            if radio.get_active():
                self.material.velocity = self.ui['velocity'].get_value()
                self.material.force = self.ui['force'].get_value()
            else:
                self.material.velocity = None
                self.material.force = None
            self.ui['apply'].set_sensitive(True)

        def on_force_changed(self,adjustment,data=None):
            self.material.force = adjustment.get_value()
            self.ui['apply'].set_sensitive(True)

        def on_velocity_changed(self,adjustment,data=None):
            self.material.velocity = adjustment.get_value()
            self.ui['apply'].set_sensitive(True)

        # Dialog buttons
        def on_close_clicked(self,button=None,data=None):
            # Cancels any changes not applied
            self.app.session.rollback()
            self.ui['dialog1'].destroy()

        def validate_form(self):
            error_msg = ''
            if self.material.name is None or self.material.name=='':
                error_msg +="Name: Cannot not be empty.\n"
            if self.material.width <= 0:
                error_msg +="Width: Must be greater than 0.\n"
            if self.material.length is not None and self.material.length <=0:
                error_msg +="Length: Must be greater than 0.\n"
            if error_msg != '':
                msg = Gtk.MessageDialog(type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                message_format="The following fields have invalid values")
                msg.format_secondary_text(error_msg+"\nPlease correct the fields and try again.")
                msg.run()
                msg.destroy()
                return False
            return True

        def on_apply_clicked(self,button,data=None):
            if self.validate_form():
                self.app.session.commit()
                self.ui['apply'].set_sensitive(False)
                self.app.flash("Applying changes...",indicator=True)
                self.app.get_widget('material','materials').emit('changed')

        def on_ok_clicked(self,button,data=None):
            if button.get_label() == 'gtk-add':
                if self.validate_form():
                    self.app.session.commit()
                    self.ui['dialog1'].destroy()
            else: # Editing
                if self.ui['apply'].get_sensitive():
                    self.on_apply_clicked(button)
                self.ui['dialog1'].destroy()

        def on_delete_clicked(self,button):
            msg = Gtk.MessageDialog(type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            message_format="Are you sure you want to delete?")
            msg.format_secondary_text("This cannot be undone.")
            response = msg.run()
            msg.destroy()
            if response == Gtk.ResponseType.YES:
                self.app.on_material_delete_activated(button,id=self.material.id)
                self.ui['dialog1'].destroy()


if __name__ == "__main__":
    # Get command line args like filename and list of selected nodes
    parser = argparse.ArgumentParser(description='A tool to generate plots from vector graphics and send them to a cutting or plotting device.')
    parser.add_argument('-o', dest='filename',type=str,help='Absolute path of a file to open')
    args = parser.parse_args()

    # Start the App
    app = Inkcut()
    app.run(filename=args.filename)
    sys.exit(0)
