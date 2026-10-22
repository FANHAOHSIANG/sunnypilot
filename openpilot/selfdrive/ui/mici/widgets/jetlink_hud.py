from openpilot.selfdrive.modeld.jetlink import hud
from openpilot.selfdrive.ui.mici.widgets.button import BigToggle
from openpilot.selfdrive.ui.mici.widgets.dialog import BigDialog
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr


class JetlinkHudToggle(BigToggle):
  def __init__(self):
    super().__init__("external HUD", "no restart needed", initial_state=hud.ENABLE.is_file(),
                     toggle_callback=self._on_toggle,
                     description="Send driving information to an external HUD through Jetlink on Mac.\n"
                                 "Install the Mac HUD adapter first and enable Jetlink.\n"
                                 "Change only while parked with the vehicle off. No restart needed.")
    self.set_enabled(lambda: not ui_state.started and hud.can_configure(ui_state.params))

  def _update_state(self):
    super()._update_state()
    # Also reflect changes made with hud_config.py, including before this UI update.
    self.set_checked(hud.ENABLE.is_file())

  def _on_toggle(self, enabled):
    try:
      # Recheck at click time, in case the vehicle started since the last frame.
      if ui_state.started:
        raise ValueError('vehicle started')
      hud.set_enabled(enabled, ui_state.params)
    except ValueError:
      gui_app.push_widget(BigDialog(tr("HUD setting unchanged"), tr("Park and turn the vehicle off before changing HUD settings.")))
    except OSError:
      gui_app.push_widget(BigDialog(tr("HUD setting not saved"), tr("Check storage permissions and try again.")))
    else:
      gui_app.push_widget(BigDialog(tr("HUD setting saved"), tr("No restart needed. Connect Jetlink on Mac with the HUD adapter installed.")))
    finally:
      self.set_checked(hud.ENABLE.is_file())
