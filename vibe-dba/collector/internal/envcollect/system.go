package envcollect

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"mariadb-collector/internal/collector"
)

func init() {
	Register(collector.FuncCollector{
		ID:        "010-system-files",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeOS && !ctx.RDS },
		Run:       collectSystemFiles,
	})
	Register(collector.FuncCollector{
		ID:        "020-system-commands",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeOS && !ctx.RDS },
		Run:       collectSystemCommands,
	})
	Register(collector.FuncCollector{
		ID:        "030-cron",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeOS && !ctx.RDS },
		Run:       collectCron,
	})
}

func collectSystemFiles(ctx *collector.Context) error {
	files := map[string]string{
		"cpuinfo":        "/proc/cpuinfo",
		"meminfo":        "/proc/meminfo",
		"swappiness":     "/proc/sys/vm/swappiness",
		"mounts":         "/proc/mounts",
		"diskstats":      "/proc/diskstats",
		"interrupts":     "/proc/interrupts",
		"softirqs":       "/proc/softirqs",
		"cmdline":        "/proc/cmdline",
		"os-release":     "/etc/os-release",
		"limits.conf":    "/etc/security/limits.conf",
		"systemd-limits": "/proc/1/limits",
	}
	for name, path := range files {
		if err := ctx.CaptureFile(name, path); err != nil && !collector.IsNotFound(err) {
			_ = ctx.AppendFile("collector-errors.log", []byte(fmt.Sprintf("%s: %v\n", name, err)))
		}
	}
	return nil
}

func collectSystemCommands(ctx *collector.Context) error {
	commands := []struct {
		out  string
		cmd  string
		args []string
	}{
		{"hostnamectl", "hostnamectl", nil},
		{"uname", "uname", []string{"-a"}},
		{"uptime", "uptime", nil},
		{"lscpu", "lscpu", nil},
		{"lsblk", "lsblk", []string{"-a", "-o", "NAME,MAJ:MIN,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,ROTA,SCHED,DISC-MAX"}},
		{"blkid", "blkid", nil},
		{"df", "df", []string{"-hT"}},
		{"free", "free", []string{"-m"}},
		{"sysctl", "sysctl", []string{"-a"}},
		{"dmesg", "dmesg", nil},
		{"lspci", "lspci", nil},
		{"ip_addr", "ip", []string{"addr"}},
		{"ip_route", "ip", []string{"route"}},
		{"ss", "ss", []string{"-s"}},
		{"netstat", "netstat", []string{"-s"}},
		{"numa", "numactl", []string{"--hardware"}},
		{"numastat", "numastat", []string{"-m"}},
		{"dmidecode", "dmidecode", nil},
		{"lvdisplay", "lvdisplay", nil},
		{"vgdisplay", "vgdisplay", nil},
		{"pvdisplay", "pvdisplay", nil},
		{"lvs_segments", "lvs", []string{"--segments"}},
	}
	for _, item := range commands {
		ctx.Logf("capture command %s", item.out)
		_ = ctx.CaptureCommand(item.out, item.cmd, item.args...)
	}
	if _, err := os.Stat("/dev/mapper"); err == nil {
		_ = ctx.CaptureCommand("dev_mapper", "ls", "-alhs", "/dev/mapper")
	}
	return nil
}

func collectCron(ctx *collector.Context) error {
	var b strings.Builder
	for _, root := range []string{"/var/spool/cron", "/etc/cron.d"} {
		_ = filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
			if err != nil || d.IsDir() {
				return nil
			}
			data, readErr := os.ReadFile(path)
			if readErr == nil {
				fmt.Fprintf(&b, "##### %s #####\n%s\n", path, data)
			}
			return nil
		})
	}
	if data, err := os.ReadFile("/etc/crontab"); err == nil {
		fmt.Fprintf(&b, "##### /etc/crontab #####\n%s\n", data)
	}
	return ctx.WriteFile("crontabs", []byte(b.String()))
}
