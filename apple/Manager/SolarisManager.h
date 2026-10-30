// C interface of SolarisManager.dylib, the macOS NetworkExtension manager (apple/README.md).
// The Python host loads it with ctypes from Solaris.app/Contents/Frameworks.
//
// Conventions:
// - Every function except solaris_manager_free returns a SolarisManagerResult. When the result
//   is not SOLARIS_MANAGER_OK and `error` is not NULL, *error receives a NUL-terminated UTF-8
//   description; otherwise *error is set to NULL.
// - Memory: buffers passed in stay owned by the caller and are read only during the call.
//   Strings and buffers handed out through pointer parameters belong to the caller, who
//   releases each with solaris_manager_free.
// - Threads: calls block until they finish or time out. NetworkExtension completes its work on
//   the main queue, so call from a background thread while the main thread runs its run loop
//   (Qt's event loop does); a call on the main thread fails at once. configure, start and stop
//   run one at a time; status and send may run alongside them, and the icloud calls alongside
//   every call.

#ifndef SOLARIS_MANAGER_H
#define SOLARIS_MANAGER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef int32_t SolarisManagerResult;
enum {
  SOLARIS_MANAGER_OK = 0,
  SOLARIS_MANAGER_FAILED = 1,
  SOLARIS_MANAGER_TIMED_OUT = 2,
  // No VPN configuration for the extension yet: configure or start creates it.
  SOLARIS_MANAGER_NOT_CONFIGURED = 3,
  // The tunnel is not connected, so the extension is not running to answer messages.
  SOLARIS_MANAGER_NOT_CONNECTED = 4,
  // Activation waits for the user to allow the extension in System Settings.
  SOLARIS_MANAGER_NEEDS_APPROVAL = 5,
  // Activation completes after the Mac restarts.
  SOLARIS_MANAGER_NEEDS_REBOOT = 6,
  // iCloud: the system is not signed in to iCloud, or its account cannot be used right now.
  SOLARIS_MANAGER_NOT_SIGNED_IN = 7,
  // iCloud: no snapshot by that name.
  SOLARIS_MANAGER_NOT_FOUND = 8,
  // iCloud: the account's storage is full.
  SOLARIS_MANAGER_QUOTA_EXCEEDED = 9,
  // iCloud: no network, or iCloud does not answer now; a later call may succeed.
  SOLARIS_MANAGER_UNREACHABLE = 10,
};

// NEVPNStatus of the configuration, plus a value for having none.
typedef int32_t SolarisTunnelStatus;
enum {
  SOLARIS_TUNNEL_NOT_CONFIGURED = 0,
  SOLARIS_TUNNEL_INVALID = 1,
  SOLARIS_TUNNEL_DISCONNECTED = 2,
  SOLARIS_TUNNEL_CONNECTING = 3,
  SOLARIS_TUNNEL_CONNECTED = 4,
  SOLARIS_TUNNEL_REASSERTING = 5,
  SOLARIS_TUNNEL_DISCONNECTING = 6,
};

// The system's iCloud account as CloudKit sees it (CKAccountStatus), plus a value for a build
// without the app's iCloud entitlements.
typedef int32_t SolarisICloudStatus;
enum {
  // Not signed for iCloud (an unsigned or ad hoc build): the library does not touch CloudKit.
  SOLARIS_ICLOUD_UNSUPPORTED = 0,
  SOLARIS_ICLOUD_AVAILABLE = 1,
  SOLARIS_ICLOUD_NO_ACCOUNT = 2,
  SOLARIS_ICLOUD_RESTRICTED = 3,
  SOLARIS_ICLOUD_TEMPORARILY_UNAVAILABLE = 4,
  SOLARIS_ICLOUD_COULD_NOT_DETERMINE = 5,
};

// Activates the system extension bundled in the app (OSSystemExtensionRequest), replacing one
// activated from another build, and waits up to timeout_seconds. When the system asks the user
// for approval, returns SOLARIS_MANAGER_NEEDS_APPROVAL at once; the request stays pending, and
// later calls wait for it again: OK once the user allows it, NEEDS_APPROVAL while they have not.
SolarisManagerResult solaris_manager_activate(double timeout_seconds, char **error);

// Creates or repairs the app's VPN configuration (NETunnelProviderManager) and enables it. The
// first save asks the user to allow adding a VPN configuration. include_all_networks sets the
// configuration's includeAllNetworks, and excludeLocalNetworks with it (D19); a connected tunnel
// keeps the value it connected with until it connects again.
SolarisManagerResult solaris_manager_configure(bool include_all_networks, char **error);

// Starts the tunnel, configuring first as solaris_manager_configure does, and waits until it is
// connected. The engine's configuration does not travel here: once connected, the host sends
// Start with solaris_manager_send (docs/decisions.md D7).
SolarisManagerResult solaris_manager_start(double timeout_seconds, bool include_all_networks,
                                           char **error);

// Stops the tunnel and waits until it is down. OK when it is not running.
SolarisManagerResult solaris_manager_stop(double timeout_seconds, char **error);

// Stores the tunnel's status in *status.
SolarisManagerResult solaris_manager_status(SolarisTunnelStatus *status, char **error);

// Sends one encoded Request to the extension (sendProviderMessage) and stores its encoded
// Response in *response and *response_length. Needs a connected tunnel.
SolarisManagerResult solaris_manager_send(const uint8_t *request, size_t request_length,
                                          double timeout_seconds, uint8_t **response,
                                          size_t *response_length, char **error);

// iCloud backup (docs/storage.md §5): snapshots in the private database of the CloudKit container
// iCloud.dev.tschen.solaris, one record of type Snapshot each. The caller names a record and
// describes it: `name` is at most 255 ASCII characters, `info` a UTF-8 string the library keeps
// as it is. The snapshot itself is the record's asset. Each call waits up to timeout_seconds;
// none needs the tunnel or the extension. A build without the iCloud entitlements fails every
// call but icloud_status.

// Stores the system's iCloud account status in *status.
SolarisManagerResult solaris_manager_icloud_status(double timeout_seconds,
                                                   SolarisICloudStatus *status, char **error);

// Saves a snapshot under name with its info, replacing one saved under that name before.
SolarisManagerResult solaris_manager_icloud_save(const char *name, const char *info,
                                                 const uint8_t *snapshot, size_t snapshot_length,
                                                 double timeout_seconds, char **error);

// Stores in *list the saved snapshots, without their content, as a UTF-8 JSON array of
// {"name": "...", "info": "..."} in no particular order.
SolarisManagerResult solaris_manager_icloud_list(double timeout_seconds, char **list,
                                                 char **error);

// Stores the snapshot saved under name in *snapshot and *snapshot_length; NOT_FOUND when there is
// none.
SolarisManagerResult solaris_manager_icloud_download(const char *name, double timeout_seconds,
                                                     uint8_t **snapshot, size_t *snapshot_length,
                                                     char **error);

// Deletes the snapshot saved under name; OK when there is none.
SolarisManagerResult solaris_manager_icloud_delete(const char *name, double timeout_seconds,
                                                   char **error);

// Releases a string or buffer from this library. NULL is ignored.
void solaris_manager_free(void *pointer);

#ifdef __cplusplus
}
#endif

#endif
