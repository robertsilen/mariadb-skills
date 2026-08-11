package envcollect

import (
	"fmt"
	"os"
	"runtime"
	"strconv"
	"strings"

	"mariadb-collector/internal/collector"
)

// Hardware identity is normalised at collection time into a single tab-separated
// key/value file, so the report has one parser regardless of the platform the
// collector ran on. The per-platform raw command output is still captured
// alongside it for anyone who wants the detail.
//
// This matters beyond completeness: total memory feeds the buffer-pool-to-RAM
// ratio, which is one of the most useful figures in the report. Without it that
// line silently disappears.
func init() {
	Register(collector.FuncCollector{
		ID:        "005-hardware",
		IfEnabled: func(ctx *collector.Context) bool { return ctx.IncludeOS && !ctx.RDS },
		Run:       collectHardware,
	})
}

func collectHardware(ctx *collector.Context) error {
	facts := map[string]string{}
	switch runtime.GOOS {
	case "darwin":
		collectHardwareDarwin(ctx, facts)
	default:
		collectHardwareLinux(ctx, facts)
	}

	if facts["cpu_cores"] == "" {
		facts["cpu_cores"] = strconv.Itoa(runtime.NumCPU())
	}
	facts["platform"] = runtime.GOOS + "/" + runtime.GOARCH

	keys := []string{
		"platform", "os_name", "os_version", "kernel",
		"cpu_model", "cpu_cores", "memory_bytes",
		"disk_root_bytes", "disk_root_ssd",
	}
	var b strings.Builder
	for _, key := range keys {
		if value := strings.TrimSpace(facts[key]); value != "" {
			fmt.Fprintf(&b, "%s\t%s\n", key, value)
		}
	}
	return ctx.WriteFile("hardware", []byte(b.String()))
}

func collectHardwareDarwin(ctx *collector.Context, facts map[string]string) {
	facts["cpu_model"] = trimmedOutput(ctx, "sysctl", "-n", "machdep.cpu.brand_string")
	facts["cpu_cores"] = trimmedOutput(ctx, "sysctl", "-n", "hw.ncpu")
	facts["memory_bytes"] = trimmedOutput(ctx, "sysctl", "-n", "hw.memsize")
	facts["kernel"] = trimmedOutput(ctx, "uname", "-sr")

	if out := trimmedOutput(ctx, "sw_vers"); out != "" {
		for _, line := range strings.Split(out, "\n") {
			name, value, found := strings.Cut(line, ":")
			if !found {
				continue
			}
			switch strings.TrimSpace(name) {
			case "ProductName":
				facts["os_name"] = strings.TrimSpace(value)
			case "ProductVersion":
				facts["os_version"] = strings.TrimSpace(value)
			}
		}
	}

	// Root filesystem size and whether it is solid state.
	if out := trimmedOutput(ctx, "diskutil", "info", "/"); out != "" {
		for _, line := range strings.Split(out, "\n") {
			name, value, found := strings.Cut(line, ":")
			if !found {
				continue
			}
			switch strings.TrimSpace(name) {
			case "Disk Size", "Volume Total Space":
				if bytes := bytesInParens(value); bytes != "" && facts["disk_root_bytes"] == "" {
					facts["disk_root_bytes"] = bytes
				}
			case "Solid State":
				facts["disk_root_ssd"] = strings.ToLower(strings.TrimSpace(value))
			}
		}
	}

	// Raw captures, for anyone who wants more than the normalised facts.
	for _, item := range [][]string{
		{"sw_vers", "sw_vers"},
		{"vm_stat", "vm_stat"},
		{"diskutil_info", "diskutil", "info", "/"},
		{"system_profiler_hardware", "system_profiler", "SPHardwareDataType"},
		{"sysctl_hw", "sysctl", "hw"},
	} {
		_ = ctx.CaptureCommand(item[0], item[1], item[2:]...)
	}
}

func collectHardwareLinux(ctx *collector.Context, facts map[string]string) {
	// x86 reports "model name"; ARM reports none of that, so count "processor"
	// lines for cores and fall back to lscpu for the model.
	if data, err := os.ReadFile("/proc/cpuinfo"); err == nil {
		cores := 0
		for _, line := range strings.Split(string(data), "\n") {
			name, value, found := strings.Cut(line, ":")
			if !found {
				continue
			}
			key := strings.TrimSpace(name)
			value = strings.TrimSpace(value)
			switch key {
			case "processor":
				cores++
			case "model name", "Model", "Hardware", "cpu model":
				if facts["cpu_model"] == "" && value != "" {
					facts["cpu_model"] = value
				}
			}
		}
		if cores > 0 {
			facts["cpu_cores"] = strconv.Itoa(cores)
		}
	}

	if facts["cpu_model"] == "" {
		if out := trimmedOutput(ctx, "lscpu"); out != "" {
			for _, line := range strings.Split(out, "\n") {
				name, value, found := strings.Cut(line, ":")
				if !found || strings.TrimSpace(name) != "Model name" {
					continue
				}
				if v := strings.TrimSpace(value); v != "" && v != "-" {
					facts["cpu_model"] = v
				}
				break
			}
		}
	}

	if data, err := os.ReadFile("/proc/meminfo"); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			if !strings.HasPrefix(line, "MemTotal:") {
				continue
			}
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				if kb, err := strconv.ParseFloat(fields[1], 64); err == nil {
					facts["memory_bytes"] = strconv.FormatInt(int64(kb*1024), 10)
				}
			}
			break
		}
	}

	if data, err := os.ReadFile("/etc/os-release"); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			name, value, found := strings.Cut(line, "=")
			if !found {
				continue
			}
			value = strings.Trim(strings.TrimSpace(value), `"`)
			switch strings.TrimSpace(name) {
			case "NAME":
				facts["os_name"] = value
			case "VERSION_ID":
				facts["os_version"] = value
			}
		}
	}

	facts["kernel"] = trimmedOutput(ctx, "uname", "-sr")
}

func trimmedOutput(ctx *collector.Context, command string, args ...string) string {
	out, err := ctx.CommandOutput(command, args...)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// bytesInParens pulls the byte count out of strings such as
// "494.4 GB (494384795648 Bytes) (exactly ...)".
func bytesInParens(value string) string {
	for {
		open := strings.Index(value, "(")
		if open < 0 {
			return ""
		}
		close := strings.Index(value[open:], ")")
		if close < 0 {
			return ""
		}
		inner := value[open+1 : open+close]
		fields := strings.Fields(inner)
		if len(fields) >= 2 && strings.EqualFold(fields[1], "Bytes") {
			if _, err := strconv.ParseInt(fields[0], 10, 64); err == nil {
				return fields[0]
			}
		}
		value = value[open+close:]
	}
}
